"""Turning rows into a file somebody can open.

The organiser spec asks for entry codes "downloadable as txt, docx, pdf and
xls", and for reports "Documents (PDF, DOCX)". Four writers in one place rather
than four in each view, because the next thing that needs a PDF should not
invent a second way of making one.

Everything returns an `HttpResponse`, never a DRF `Response`. A DRF Response
renders through the JSON renderer and the file arrives as a quoted string: the
`views_export` module carries a comment about exactly that, and there is a test
in this repo for the same fault on a CSV.
"""
import csv
import io

from django.http import HttpResponse


def _attach(body, content_type, filename):
    response = HttpResponse(body, content_type=content_type)
    # `filename*` as well, so a tournament with a non-ASCII name downloads with
    # its own name rather than a browser's guess.
    response['Content-Disposition'] = (
        'attachment; filename="%s"; filename*=UTF-8\'\'%s'
        % (filename.encode('ascii', 'ignore').decode() or 'download', filename)
    )
    return response


def as_txt(lines, filename):
    """One thing per line and nothing else.

    The usual next step is pasting them into a message, so a header would be
    something everybody has to delete.
    """
    body = '\n'.join(str(line) for line in lines) + '\n'
    return _attach(body, 'text/plain; charset=utf-8', filename)


def as_csv(header, rows, filename):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if header:
        writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return _attach(buffer.getvalue(), 'text/csv; charset=utf-8', filename)


def as_xlsx(header, rows, filename, sheet_title='Sheet1'):
    """A real spreadsheet, not a CSV named .xls.

    Excel warns loudly when a file's contents do not match its extension, and
    the person seeing that warning is a customer of whoever sent them the file.
    """
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = sheet_title[:31]          # Excel's own limit
    if header:
        sheet.append(list(header))
    for row in rows:
        sheet.append(list(row))

    # Width from the content, because a column of codes rendered as ##### is a
    # spreadsheet somebody has to fix before they can read it.
    for index, column in enumerate(sheet.columns, start=1):
        longest = max((len(str(cell.value or '')) for cell in column), default=8)
        sheet.column_dimensions[chr(64 + index) if index <= 26 else 'A'].width = \
            min(60, max(10, longest + 2))

    buffer = io.BytesIO()
    book.save(buffer)
    return _attach(
        buffer.getvalue(),
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        filename)


def as_docx(title, header, rows, filename, note=''):
    """A document, with the rows as a real table.

    A table rather than tab separated text: the reason somebody asks for docx
    rather than csv is that they are going to paste it into something else, and
    a table survives that.
    """
    from docx import Document

    document = Document()
    document.add_heading(title, level=1)
    if note:
        document.add_paragraph(note)

    table = document.add_table(rows=1, cols=len(header) if header else 1)
    table.style = 'Table Grid'
    if header:
        cells = table.rows[0].cells
        for index, name in enumerate(header):
            cells[index].text = str(name)
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            if index < len(cells):
                cells[index].text = '' if value is None else str(value)

    buffer = io.BytesIO()
    document.save(buffer)
    return _attach(
        buffer.getvalue(),
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        filename)


def as_pdf(title, header, rows, filename, note=''):
    """A PDF that paginates.

    Drawn with platypus rather than the canvas: a canvas needs the caller to
    work out where the page ends, and the first long tournament would have run
    off the bottom of page one with nothing saying so.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=title)

    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles['Title'])]
    if note:
        story.append(Paragraph(note, styles['Normal']))
    story.append(Spacer(1, 8))

    data = ([list(header)] if header else []) + [
        ['' if value is None else str(value) for value in row] for row in rows
    ]
    if not data:
        data = [['Nothing to show']]

    table = Table(data, repeatRows=1 if header else 0)
    table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        # A header that reads as a header without a stroke around every cell.
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#212225')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1),
         [colors.white, colors.HexColor('#f4f4f5')]),
    ]))
    story.append(table)

    document.build(story)
    return _attach(buffer.getvalue(), 'application/pdf', filename)


#: What `?as=` accepts, and the extension each one produces. One map, so a view
#: cannot offer a format the writer does not have.
FORMATS = {
    'txt': 'txt',
    'csv': 'csv',
    'xlsx': 'xlsx',
    'xls': 'xlsx',        # what people type, and what they actually want
    'docx': 'docx',
    'doc': 'docx',
    'pdf': 'pdf',
}
