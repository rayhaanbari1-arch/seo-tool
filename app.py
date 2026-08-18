import csv
import os
import io
import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from flask import Flask, render_template, request, Response, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)

app = Flask(__name__)

db_path = os.environ.get('DATABASE_URL', 'sqlite:///seo_tracker.db')
app.config['SQLALCHEMY_DATABASE_URI'] = db_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB upload limit

db = SQLAlchemy(app)


# ─── Models ──────────────────────────────────────────────────────────────────

class Client(db.Model):
    __tablename__ = 'clients'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    groups = db.relationship('LinkGroup', backref='client', cascade='all, delete-orphan', order_by='LinkGroup.id')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'groups': [g.to_dict() for g in self.groups]
        }


class LinkGroup(db.Model):
    __tablename__ = 'link_groups'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    group_name = db.Column(db.String(200), nullable=False)
    links = db.relationship('PageLink', backref='group', cascade='all, delete-orphan', order_by='PageLink.id')

    def to_dict(self):
        return {
            'id': self.id,
            'group_name': self.group_name,
            'links': [l.to_dict() for l in self.links]
        }


class PageLink(db.Model):
    __tablename__ = 'page_links'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('link_groups.id'), nullable=False)
    project_name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(2000), nullable=False)
    page_type = db.Column(db.String(50), nullable=False, default='lp')  # 'lp' or 'project'

    def to_dict(self):
        return {
            'id': self.id,
            'project_name': self.project_name,
            'url': self.url,
            'page_type': self.page_type
        }


class GTMContainer(db.Model):
    __tablename__ = 'gtm_containers'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, unique=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    mapping_count = db.Column(db.Integer, default=0)
    mappings_json = db.Column(db.Text, nullable=False, default='[]')

    client = db.relationship('Client', backref=db.backref('gtm_container', uselist=False))

    def get_map(self) -> dict:
        """Return {event_name: css_selector} for fast lookup in screenshot engine.

        For id-type mappings, also indexes by selector_value (the raw element id),
        because GA4 records the element's id attribute as the Click ID — which is
        what the Excel sheet contains — not the human-readable GTM event name.
        """
        result = {}
        for m in json.loads(self.mappings_json):
            result[m['event_name']] = m['css_selector']
            if m.get('selector_type') == 'id' and m.get('selector_value'):
                result[m['selector_value']] = m['css_selector']
        return result

    def to_dict(self):
        return {
            'mapping_count': self.mapping_count,
            'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None,
            'mappings': json.loads(self.mappings_json),
        }


class ReportJob(db.Model):
    __tablename__ = 'report_jobs'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    client_name = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending')  # pending, running, completed, failed
    error = db.Column(db.Text, nullable=True)
    report_html = db.Column(db.Text, nullable=True)
    report_name = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    client = db.relationship('Client', backref='reports')

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'client_name': self.client_name,
            'status': self.status,
            'error': self.error,
            'report_name': self.report_name,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }


# ─── Background Report Worker ────────────────────────────────────────────────

_job_log = logging.getLogger('report_job')
_cancel_flags: set[int] = set()   # job_ids that have been cancelled


def _is_cancelled(job_id: int) -> bool:
    return job_id in _cancel_flags


def _run_report_job(job_id, excel_bytes, links):
    """Run report generation in a background thread."""
    with app.app_context():
        job = db.session.get(ReportJob, job_id)
        if not job:
            return

        job.status = 'running'
        db.session.commit()
        t_start = time.time()
        _job_log.info('job %d started  client=%r  links=%d  excel_size=%d KB',
                      job_id, job.client_name, len(links), len(excel_bytes) // 1024)

        try:
            from src.excel_parser import parse_excel
            from src.screenshot_engine import capture_all_pages
            from src.report_generator import generate_report

            t1 = time.time()
            project_data = parse_excel(io.BytesIO(excel_bytes))
            _job_log.info('job %d: excel parsed in %.1fs  sheets=%d',
                          job_id, time.time() - t1, len(project_data))

            if _is_cancelled(job_id):
                raise InterruptedError('cancelled')

            t2 = time.time()
            gtm_container = GTMContainer.query.filter_by(client_id=job.client_id).first()
            gtm_mappings = gtm_container.get_map() if gtm_container else {}
            _job_log.info('job %d: GTM mappings loaded  count=%d', job_id, len(gtm_mappings))

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                screenshots = loop.run_until_complete(
                    capture_all_pages(links, project_data, gtm_mappings)
                )
            finally:
                loop.close()
            _job_log.info('job %d: screenshots done in %.1fs  pages_captured=%d',
                          job_id, time.time() - t2, sum(len(v) for v in screenshots.values()))

            if _is_cancelled(job_id):
                raise InterruptedError('cancelled')

            t3 = time.time()
            html = generate_report(job.client_name, project_data, screenshots, links)
            _job_log.info('job %d: report generated in %.1fs  html_size=%d KB',
                          job_id, time.time() - t3, len(html) // 1024)

            job.report_html = html
            job.status = 'completed'
            job.completed_at = datetime.utcnow()

            now = datetime.now()
            job.report_name = f"{job.client_name} Event report {now.day} ({now.strftime('%B')}) {now.year}"
            _job_log.info('job %d COMPLETED in %.1fs total', job_id, time.time() - t_start)

        except InterruptedError:
            _job_log.info('job %d CANCELLED after %.1fs', job_id, time.time() - t_start)
            job.status = 'cancelled'
            job.completed_at = datetime.utcnow()

        except Exception as e:
            _job_log.exception('job %d FAILED after %.1fs: %s', job_id, time.time() - t_start, e)
            job.status = 'failed'
            job.error = str(e)
            job.completed_at = datetime.utcnow()

        finally:
            _cancel_flags.discard(job_id)

        db.session.commit()


# ─── Client API ──────────────────────────────────────────────────────────────

@app.route('/api/clients', methods=['GET'])
def get_clients():
    clients = Client.query.order_by(Client.name).all()
    return jsonify([c.to_dict() for c in clients])


@app.route('/api/clients', methods=['POST'])
def create_client():
    data = request.get_json()
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    client = Client(name=name)
    db.session.add(client)
    db.session.commit()
    return jsonify(client.to_dict()), 201


@app.route('/api/clients/<int:client_id>', methods=['PUT'])
def update_client(client_id):
    client = Client.query.get_or_404(client_id)
    data = request.get_json()
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    client.name = name
    db.session.commit()
    return jsonify(client.to_dict())


@app.route('/api/clients/<int:client_id>', methods=['DELETE'])
def delete_client(client_id):
    client = Client.query.get_or_404(client_id)
    db.session.delete(client)
    db.session.commit()
    return '', 204


# ─── Client CSV import ────────────────────────────────────────────────────────

CSV_TEMPLATE_ROWS = [
    ['client_name', 'group_name', 'project_name', 'url', 'page_type'],
    ['Acme Corp', 'Flagship', 'Acme Tower', 'https://acmecorp.com/lp/tower/', 'lp'],
    ['Acme Corp', 'Flagship', 'Acme Tower', 'https://acmecorp.com/projects/tower/', 'project'],
    ['Acme Corp', 'Suburbs', 'Acme Gardens', 'https://acmecorp.com/lp/gardens/', 'lp'],
]

@app.route('/api/clients/csv-template', methods=['GET'])
def download_csv_template():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(CSV_TEMPLATE_ROWS)
    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename="client_import_template.csv"'}
    )


@app.route('/api/clients/import-csv', methods=['POST'])
def import_clients_csv():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400

    try:
        text = f.read().decode('utf-8-sig')   # handle BOM from Excel CSV exports
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
    except Exception as e:
        return jsonify({'error': f'Could not parse CSV: {e}'}), 400

    required = {'client_name', 'group_name', 'project_name', 'url', 'page_type'}
    if not rows:
        return jsonify({'error': 'CSV is empty'}), 400
    missing = required - {k.strip().lower() for k in rows[0].keys()}
    if missing:
        return jsonify({'error': f'Missing columns: {", ".join(sorted(missing))}'}), 400

    created_clients = 0
    created_links = 0
    errors = []

    # Group rows by client_name then group_name
    for i, row in enumerate(rows, start=2):
        client_name  = (row.get('client_name')  or '').strip()
        group_name   = (row.get('group_name')   or '').strip()
        project_name = (row.get('project_name') or '').strip()
        url          = (row.get('url')          or '').strip()
        page_type    = (row.get('page_type')    or 'lp').strip().lower()

        if not client_name or not url:
            errors.append(f'Row {i}: client_name and url are required — skipped')
            continue
        if page_type not in ('lp', 'project'):
            errors.append(f'Row {i}: page_type must be "lp" or "project", got "{page_type}" — skipped')
            continue

        client = Client.query.filter_by(name=client_name).first()
        if not client:
            client = Client(name=client_name)
            db.session.add(client)
            db.session.flush()
            created_clients += 1

        group = LinkGroup.query.filter_by(client_id=client.id, group_name=group_name).first()
        if not group:
            group = LinkGroup(client_id=client.id, group_name=group_name or 'Default')
            db.session.add(group)
            db.session.flush()

        link = PageLink(
            group_id=group.id,
            project_name=project_name or client_name,
            url=url,
            page_type=page_type,
        )
        db.session.add(link)
        created_links += 1

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Database error: {e}'}), 500

    return jsonify({
        'created_clients': created_clients,
        'created_links': created_links,
        'errors': errors,
    }), 200


# ─── Group API ────────────────────────────────────────────────────────────────

@app.route('/api/clients/<int:client_id>/groups', methods=['POST'])
def create_group(client_id):
    Client.query.get_or_404(client_id)
    data = request.get_json()
    group_name = (data.get('group_name') or '').strip()
    if not group_name:
        return jsonify({'error': 'Group name is required'}), 400
    group = LinkGroup(client_id=client_id, group_name=group_name)
    db.session.add(group)
    db.session.commit()
    return jsonify(group.to_dict()), 201


@app.route('/api/groups/<int:group_id>', methods=['PUT'])
def update_group(group_id):
    group = LinkGroup.query.get_or_404(group_id)
    data = request.get_json()
    group_name = (data.get('group_name') or '').strip()
    if not group_name:
        return jsonify({'error': 'Group name is required'}), 400
    group.group_name = group_name
    db.session.commit()
    return jsonify(group.to_dict())


@app.route('/api/groups/<int:group_id>', methods=['DELETE'])
def delete_group(group_id):
    group = LinkGroup.query.get_or_404(group_id)
    db.session.delete(group)
    db.session.commit()
    return '', 204


# ─── Link API ────────────────────────────────────────────────────────────────

@app.route('/api/groups/<int:group_id>/links', methods=['POST'])
def create_link(group_id):
    LinkGroup.query.get_or_404(group_id)
    data = request.get_json()
    project_name = (data.get('project_name') or '').strip()
    url = (data.get('url') or '').strip()
    page_type = (data.get('page_type') or 'lp').strip()
    if not project_name or not url:
        return jsonify({'error': 'project_name and url are required'}), 400
    link = PageLink(group_id=group_id, project_name=project_name, url=url, page_type=page_type)
    db.session.add(link)
    db.session.commit()
    return jsonify(link.to_dict()), 201


@app.route('/api/links/<int:link_id>', methods=['PUT'])
def update_link(link_id):
    link = PageLink.query.get_or_404(link_id)
    data = request.get_json()
    link.project_name = (data.get('project_name') or link.project_name).strip()
    link.url = (data.get('url') or link.url).strip()
    link.page_type = (data.get('page_type') or link.page_type).strip()
    db.session.commit()
    return jsonify(link.to_dict())


@app.route('/api/links/<int:link_id>', methods=['DELETE'])
def delete_link(link_id):
    link = PageLink.query.get_or_404(link_id)
    db.session.delete(link)
    db.session.commit()
    return '', 204


# ─── Bulk save (full client state) ───────────────────────────────────────────

@app.route('/api/clients/<int:client_id>/save', methods=['POST'])
def save_client_full(client_id):
    """Replace all groups/links for a client with the posted data."""
    client = Client.query.get_or_404(client_id)
    data = request.get_json()

    client.name = (data.get('name') or client.name).strip()

    # Delete existing groups (cascade deletes links)
    for group in client.groups:
        db.session.delete(group)
    db.session.flush()

    for g in data.get('groups', []):
        group = LinkGroup(client_id=client.id, group_name=(g.get('group_name') or '').strip())
        db.session.add(group)
        db.session.flush()
        for l in g.get('links', []):
            link = PageLink(
                group_id=group.id,
                project_name=(l.get('project_name') or '').strip(),
                url=(l.get('url') or '').strip(),
                page_type=(l.get('page_type') or 'lp').strip()
            )
            db.session.add(link)

    db.session.commit()
    return jsonify(client.to_dict())


# ─── Generate Report (Background) ────────────────────────────────────────────

@app.route('/generate', methods=['POST'])
def generate():
    client_id = request.form.get('client_id')
    if not client_id:
        return jsonify({'error': 'No client selected'}), 400

    excel_file = request.files.get('excel')
    if not excel_file:
        return jsonify({'error': 'No Excel file uploaded'}), 400

    client = Client.query.get_or_404(int(client_id))

    # Read excel bytes so we can pass to background thread
    excel_bytes = excel_file.read()

    # Collect all links from this client
    links = []
    for group in client.groups:
        for link in group.links:
            links.append({
                'project_name': link.project_name,
                'url': link.url,
                'page_type': link.page_type,
                'group_name': group.group_name
            })

    # Create job record
    job = ReportJob(client_id=client.id, client_name=client.name)
    db.session.add(job)
    db.session.commit()

    # Launch background thread
    thread = threading.Thread(target=_run_report_job, args=(job.id, excel_bytes, links))
    thread.daemon = True
    thread.start()

    return jsonify(job.to_dict()), 202


# ─── Report Jobs API ────────────────────────────────────────────────────────

@app.route('/api/reports', methods=['GET'])
def list_reports():
    jobs = ReportJob.query.order_by(ReportJob.created_at.desc()).all()
    return jsonify([j.to_dict() for j in jobs])


@app.route('/api/reports/<int:job_id>', methods=['GET'])
def get_report_status(job_id):
    job = db.session.get(ReportJob, job_id)
    if not job:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(job.to_dict())


@app.route('/api/reports/<int:job_id>/download', methods=['GET'])
def download_report(job_id):
    job = db.session.get(ReportJob, job_id)
    if not job:
        return jsonify({'error': 'Not found'}), 404
    if job.status != 'completed' or not job.report_html:
        return jsonify({'error': 'Report not ready'}), 400

    filename = f"{job.report_name or 'report'}.html"
    return Response(
        job.report_html,
        mimetype='text/html',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@app.route('/api/reports/<int:job_id>/view', methods=['GET'])
def view_report(job_id):
    job = db.session.get(ReportJob, job_id)
    if not job:
        return 'Not found', 404
    if job.status != 'completed' or not job.report_html:
        return 'Report not ready', 400
    return Response(job.report_html, mimetype='text/html')


@app.route('/api/reports/<int:job_id>/cancel', methods=['POST'])
def cancel_report(job_id):
    job = db.session.get(ReportJob, job_id)
    if not job:
        return jsonify({'error': 'Not found'}), 404
    if job.status not in ('pending', 'running'):
        return jsonify({'error': f'Job is already {job.status}'}), 400
    _cancel_flags.add(job_id)
    _job_log.info('job %d cancel requested', job_id)
    return jsonify({'ok': True})


@app.route('/api/reports/<int:job_id>', methods=['DELETE'])
def delete_report(job_id):
    job = db.session.get(ReportJob, job_id)
    if not job:
        return jsonify({'error': 'Not found'}), 404
    db.session.delete(job)
    db.session.commit()
    return '', 204


# ─── GTM Container API ───────────────────────────────────────────────────────

@app.route('/api/clients/<int:client_id>/gtm', methods=['GET'])
def get_gtm(client_id):
    Client.query.get_or_404(client_id)
    container = GTMContainer.query.filter_by(client_id=client_id).first()
    if not container:
        return jsonify({'mapping_count': 0, 'uploaded_at': None, 'mappings': []})
    return jsonify(container.to_dict())


_gtm_log = logging.getLogger('gtm_upload')

@app.route('/api/clients/<int:client_id>/gtm', methods=['POST'])
def upload_gtm(client_id):
    client = Client.query.get_or_404(client_id)
    gtm_file = request.files.get('gtm_json')
    if not gtm_file:
        _gtm_log.warning('upload rejected for client %d (%r) — no file in request',
                         client_id, client.name)
        return jsonify({'error': 'No file uploaded'}), 400

    raw_bytes = gtm_file.read()
    _gtm_log.info('GTM upload for client %d (%r)  file=%r  size=%d KB',
                  client_id, client.name, gtm_file.filename, len(raw_bytes) // 1024)

    try:
        data = json.loads(raw_bytes)
    except json.JSONDecodeError as e:
        _gtm_log.error('GTM upload failed for client %d — invalid JSON: %s', client_id, e)
        return jsonify({'error': f'Invalid JSON: {e}'}), 400

    try:
        from src.gtm_parser import parse_gtm_container
        mappings = parse_gtm_container(data)
    except ValueError as e:
        _gtm_log.error('GTM upload failed for client %d — container parse error: %s',
                       client_id, e)
        return jsonify({'error': str(e)}), 400

    if len(mappings) == 0:
        _gtm_log.warning('GTM upload for client %d produced 0 mappings — '
                         'container parsed OK but no usable click triggers found. '
                         'Check gtm_parser logs above for skip reasons.',
                         client_id)

    container = GTMContainer.query.filter_by(client_id=client_id).first()
    if not container:
        container = GTMContainer(client_id=client_id)
        db.session.add(container)

    container.mappings_json = json.dumps(mappings)
    container.mapping_count = len(mappings)
    container.uploaded_at = datetime.utcnow()

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        _gtm_log.exception('GTM upload DB commit failed for client %d: %s', client_id, e)
        return jsonify({'error': 'Failed to save mappings — database error'}), 500

    _gtm_log.info('GTM upload saved for client %d (%r): %d mappings stored',
                  client_id, client.name, len(mappings))
    return jsonify(container.to_dict()), 200


@app.route('/api/clients/<int:client_id>/gtm', methods=['DELETE'])
def delete_gtm(client_id):
    container = GTMContainer.query.filter_by(client_id=client_id).first()
    if container:
        db.session.delete(container)
        db.session.commit()
    return '', 204


# ─── Validate Excel sheet names against client links ─────────────────────────

@app.route('/validate', methods=['POST'])
def validate():
    from src.excel_parser import get_sheet_names

    excel_file = request.files.get('excel')
    client_id = request.form.get('client_id')
    if not excel_file or not client_id:
        return jsonify({'error': 'Missing file or client'}), 400

    client = Client.query.get_or_404(int(client_id))
    sheet_names = get_sheet_names(excel_file)

    client_projects = set()
    for group in client.groups:
        for link in group.links:
            client_projects.add(link.project_name.strip().lower())

    matched = []
    unmatched_sheets = []
    for sheet in sheet_names:
        if sheet.strip().lower() in client_projects:
            matched.append(sheet)
        else:
            unmatched_sheets.append(sheet)

    unmatched_links = []
    for proj in client_projects:
        if proj not in [s.strip().lower() for s in sheet_names]:
            unmatched_links.append(proj)

    return jsonify({
        'sheet_names': sheet_names,
        'matched': matched,
        'unmatched_sheets': unmatched_sheets,
        'unmatched_links': unmatched_links
    })


# ─── Main UI ─────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


# ─── Init ────────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)
