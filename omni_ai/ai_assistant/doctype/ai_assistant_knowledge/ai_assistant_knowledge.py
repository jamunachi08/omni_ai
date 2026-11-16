import frappe
from frappe import _
from frappe.model.document import Document


class AIAssistantKnowledge(Document):
	def validate(self):
		from omni_ai.engine.files import extract_text
		from omni_ai.tools import knowledge, memory

		self.title = " ".join((self.title or "").split())[:140]
		file_changed = self.is_new() or self.has_value_changed("file")
		if self.file and (file_changed or not (self.content or "").strip()):
			file_name = frappe.db.get_value("File", {"file_url": self.file}, "name")
			if not file_name:
				frappe.throw(_("The attached file could not be found. Attach it again."))
			try:
				self.content = extract_text(frappe.get_doc("File", file_name), knowledge.MAX_CONTENT_CHARS)
			except Exception as exc:
				frappe.throw(_("The file could not be read: {0}").format(str(exc)[:200]))
		self.content = (self.content or "").strip()
		if len(self.content) < 20:
			frappe.throw(_("Attach a file or paste the text of the document."))
		if memory.looks_like_secret(self.content[:20000]) and "-----BEGIN" in self.content:
			frappe.throw(_("This text contains a private key. Remove it before adding the document."))

		passages = knowledge.split_passages(self.content)
		chunks = [{"t": text} for text in passages]
		self.embedding_model = ""
		note = ""
		if memory.embeddings_on():
			try:
				for start in range(0, len(passages), 32):
					batch = passages[start : start + 32]
					for offset, vector in enumerate(memory.embed_texts([f"{self.title}\n{text}" for text in batch])):
						chunks[start + offset]["v"] = [round(v, 4) for v in vector]
				self.embedding_model = frappe.get_cached_doc("AI Assistant Settings").embeddings_model
			except Exception:
				for chunk in chunks:
					chunk.pop("v", None)
				note = ", searched by words (the embeddings service could not be reached)"
		self.chunks = frappe.as_json(chunks, indent=None)
		self.characters = len(self.content)
		cut = " (the text was cut at the size limit)" if len(self.content) >= knowledge.MAX_CONTENT_CHARS else ""
		self.status = f"Ready: {len(chunks)} passages{note}{cut}"
