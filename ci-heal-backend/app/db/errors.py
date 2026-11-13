"""Errors raised by the repositories."""


class InvalidStatusTransition(Exception):
    """A failure was asked to move to a processing status its state machine forbids."""
