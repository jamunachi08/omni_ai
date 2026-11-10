"""Turn files the user attached in the chat into content the model can read."""

import base64
import csv
import io
import os

import frappe

from omni_ai.utils import clip

IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
TEXT_TYPES = {
	"txt", "md", "csv", "tsv", "json", "xml", "html", "htm", "py", "js", "ts", "sql", "log", "yaml", "yml",
	"ini", "css", "jinja", "sh",
}  # fmt: skip
SHEET_TYPES = {"xlsx", "xlsm"}
MAX_IMAGE_BYTES = 3_700_000
MAX_PDF_BYTES = 20_000_000
MAX_TEXT_CHARS = 150_000
MAX_SHEET_ROWS = 1500
MAX_FILES = 8
ALLOWED = set(IMAGE_TYPES) | TEXT_TYPES | SHEET_TYPES | {"pdf", "docx"}


def extension(file_name: str) -> str:
	return os.path.splitext(file_name or "")[1].lower().lstrip(".")


def validate_attachments(file_names, user: str) -> list:
	"""Check ownership and type; return compact references stored on the message."""
	refs = []
	for name in (file_names or [])[:MAX_FILES]:
		doc = frappe.db.get_value(
			"File", name, ["name", "file_name", "file_url", "file_size", "owner", "is_private"], as_dict=True
		)
		if not doc or doc.owner != user:
			frappe.throw("One of the attached files could not be found.", frappe.PermissionError)
		ext = extension(doc.file_name)
		if ext not in ALLOWED:
			frappe.throw(f"Files of type .{ext} are not supported in the assistant.")
		refs.append({"file": doc.name, "file_name": doc.file_name, "file_url": doc.file_url, "size": doc.file_size})
	return refs


def _read_bytes(file_doc) -> bytes:
	path = file_doc.get_full_path()
	with open(path, "rb") as handle:
		return handle.read()


def attachment_blocks(refs, user: str) -> list:
	"""API content blocks for a message's attachments. Problems become a short text note instead of an error."""
	blocks = []
	for ref in refs or []:
		name = ref.get("file_name") or "file"
		try:
			file_doc = frappe.get_doc("File", ref.get("file"))
			if file_doc.owner != user:
				raise frappe.PermissionError
			blocks.extend(_blocks_for(file_doc, name))
		except frappe.DoesNotExistError:
			blocks.append({"type": "text", "text": f"[Attached file '{name}' is no longer available.]"})
		except Exception as exc:
			blocks.append({"type": "text", "text": f"[Attached file '{name}' could not be read: {clip(exc, 200)}]"})
	return blocks


def _blocks_for(file_doc, name: str) -> list:
	ext = extension(name)
	data = _read_bytes(file_doc)
	if ext in IMAGE_TYPES:
		if len(data) > MAX_IMAGE_BYTES:
			return [{"type": "text", "text": f"[Image '{name}' is too large to read ({len(data) // 1024} KB). Ask for a smaller version.]"}]
		return [
			{"type": "text", "text": f"Attached image: {name}"},
			{"type": "image", "source": {"type": "base64", "media_type": IMAGE_TYPES[ext], "data": base64.b64encode(data).decode()}},
		]
	if ext == "pdf":
		if len(data) > MAX_PDF_BYTES:
			return [{"type": "text", "text": f"[PDF '{name}' is too large to read. Ask for a smaller file or the relevant pages.]"}]
		return [
			{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(data).decode()}, "title": name},
		]
	if ext in SHEET_TYPES:
		return [{"type": "text", "text": f"Attached spreadsheet: {name}\n\n{_sheet_text(data)}"}]
	if ext == "docx":
		return [{"type": "text", "text": f"Attached document: {name}\n\n{_docx_text(data)}"}]
	text = data.decode("utf-8", errors="replace")
	return [{"type": "text", "text": f"Attached file: {name}\n\n{clip(text, MAX_TEXT_CHARS)}"}]


def _sheet_text(data: bytes) -> str:
	from openpyxl import load_workbook

	workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
	out = []
	for sheet in workbook.worksheets[:4]:
		buffer = io.StringIO()
		writer = csv.writer(buffer)
		count = 0
		for row in sheet.iter_rows(values_only=True):
			if not any(cell not in (None, "") for cell in row):
				continue
			writer.writerow(["" if cell is None else cell for cell in row[:60]])
			count += 1
			if count >= MAX_SHEET_ROWS:
				writer.writerow([f"… more rows not shown (limit {MAX_SHEET_ROWS})"])
				break
		out.append(f"## Sheet: {sheet.title}\n{buffer.getvalue()}")
	return clip("\n".join(out), MAX_TEXT_CHARS)


def _docx_text(data: bytes) -> str:
	"""Plain text of a .docx without extra dependencies (it is a zip of XML)."""
	import re
	import zipfile

	with zipfile.ZipFile(io.BytesIO(data)) as archive:
		xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
	xml = re.sub(r"</w:p>", "\n", xml)
	xml = re.sub(r"<w:tab/>", "\t", xml)
	text = re.sub(r"<[^>]+>", "", xml)
	text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'")
	return clip(text.strip(), MAX_TEXT_CHARS)
