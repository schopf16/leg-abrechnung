"""Reading an uploaded file, in one place because it broke once."""


async def read_uploaded_file(file) -> tuple[str, bytes]:
    """Read one uploaded file into a `(name, content)` pair."""
    return file.name, await file.read()
