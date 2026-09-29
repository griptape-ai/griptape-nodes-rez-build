"""The error every command reports to the administrator and exits non-zero on."""


class BuildError(Exception):
    """A build, write, or check that failed, with a message written for the person running it.

    Messages follow "Attempted to X. Failed because Y." and say what to do where there is
    something to do.
    """


class ResolveError(BuildError):
    """uv could not resolve the requested pip packages together."""
