__version__ = "1.4.0"


def ask(prompt, system=None, max_tokens=2000, model=None, user=None, timeout=120):
	"""Ask the configured AI provider one question from any app or server-side code. See omni_ai.engine.simple."""
	from omni_ai.engine.simple import ask as _ask

	return _ask(prompt, system=system, max_tokens=max_tokens, model=model, user=user, timeout=timeout)
