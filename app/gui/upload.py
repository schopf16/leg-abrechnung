"""Reading an uploaded file, in one place because it broke once.

This function used to sit in `app/gui/pages/email_dispatch.py`, and it is
the one that failed silently when `nicegui` went from 3.15 to 3.16: the
upload API changed from `event.name`/`event.content.read()` to
`event.file.name`/`await event.file.read()`, the old call raised
`AttributeError` inside the handler, NiceGUI logged and swallowed it, and
for nine days every broadcast went out **without its attachment** with
nothing saying so (see CLAUDE.md, "A page rendering is not evidence that it
works").

Now a second page uploads files -- the Textbausteine carry their own
attachments -- so it moved here rather than being written out twice. One
place to break, one place a framework bump has to be checked, and
`tests/test_email_attachments.py` is the contract test that pins the
framework's event shape for both callers.
"""


async def read_uploaded_file(file) -> tuple[str, bytes]:
    """Read one uploaded file into a `(name, content)` pair.

    Deliberately a module-level function rather than inline in an upload
    handler: reading the event is exactly where this broke silently once,
    and a handler's body cannot be called from a test.

    Args:
        file: NiceGUI `FileUpload` (`UploadEventArguments.file`) -- needs
            `.name` and an awaitable `.read()`.

    Returns:
        `(filename, content bytes)`.
    """
    return file.name, await file.read()
