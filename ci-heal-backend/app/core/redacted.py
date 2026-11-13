"""A string that has already been cleared to send to a model.

`typing.NewType` is erased at runtime, so a plain `str` would still be accepted by the model
client. This is a real `str` subclass instead: `RedactedText("secret")` raises.

Two constructors exist, and they mean different things:

- `trusted` is for static prompt text we wrote ourselves.
- `_from_redactor` is for text that went through the redactor (logs, diffs, model output).

`join` and `format` only combine values that are already `RedactedText`, so a prompt can be
assembled without turning an unredacted log back into a plain string.
"""

from collections.abc import Sequence


class RedactedText(str):
    """Text cleared to send to a model. A plain `str` is not this type."""

    def __new__(cls, content: str) -> "RedactedText":
        """Reject direct construction. Use `trusted` or the redactor."""
        raise TypeError(
            "RedactedText cannot be built from a plain string. "
            "Use RedactedText.trusted() for prompt text we wrote, "
            "or redact()/prepare_log() for everything else."
        )

    @classmethod
    def trusted(cls, content: str) -> "RedactedText":
        """Mark static text we authored as safe to send.

        This is the only constructor for prompt instructions. Do not pass a log, a diff,
        a file, a webhook field, or model output: those go through the redactor.

        Args:
            content: A literal we wrote, such as a system prompt or a delimiter line.

        Returns:
            That same text, typed so the model client will accept it.
        """
        if type(content) is not str:
            raise TypeError("trusted() takes a plain str of text we authored")
        return str.__new__(cls, content)

    @classmethod
    def _from_redactor(cls, content: str) -> "RedactedText":
        """Wrap text the redactor just produced. Not for callers outside that module."""
        return str.__new__(cls, content)

    @classmethod
    def join(cls, parts: Sequence["RedactedText"], *, sep: str = "") -> "RedactedText":
        """Concatenate cleared pieces. The separator is static text we authored.

        Args:
            parts: Pieces that are already `RedactedText` (trusted or redacted).
            sep: Inserted between pieces. A plain string, because we wrote it.

        Returns:
            The joined text, still `RedactedText`.
        """
        if type(sep) is not str:
            raise TypeError("join separator must be a plain str")
        for part in parts:
            if type(part) is not RedactedText:
                raise TypeError("join() only accepts RedactedText pieces")
        return str.__new__(cls, sep.join(parts))

    @classmethod
    def format(cls, template: str, /, **values: "RedactedText") -> "RedactedText":
        """Fill a static template. Every placeholder value must already be cleared.

        The template is text we wrote. Values are substituted once, so braces inside a
        value are not read as further placeholders.

        Args:
            template: A literal such as ``"hint: {hint}."``.
            **values: One `RedactedText` per placeholder name.

        Returns:
            The filled text, still `RedactedText`.
        """
        if type(template) is not str:
            raise TypeError("format template must be a plain str")
        for name, value in values.items():
            if type(value) is not RedactedText:
                raise TypeError(f"format value {name!r} must be RedactedText")
        try:
            rendered = template.format(**values)
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError("prompt template could not be filled") from exc
        return str.__new__(cls, rendered)

    def escape_delimiter(self, marker: str) -> "RedactedText":
        """Break a delimiter that appears inside this already-cleared text.

        A log that contains the closing marker would otherwise end the untrusted block
        early. The marker's first character is separated with a space, so the original
        text stays readable and no longer matches the delimiter.

        Args:
            marker: The closing marker the prompt uses, for example ``<<<END_UNTRUSTED_LOG>>>``.

        Returns:
            This text with every occurrence of `marker` broken.
        """
        if type(marker) is not str or marker == "":
            raise ValueError("marker must be a non-empty string")
        broken = marker[0] + " " + marker[1:]
        return str.__new__(type(self), str.replace(self, marker, broken))
