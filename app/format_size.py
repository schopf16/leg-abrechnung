"""One way to write a byte count, wherever the app shows one."""


def format_size(num_bytes: int) -> str:
    """Render a byte count the way a German reader sizes up a file."""
    size = max(0, num_bytes)
    if size < 1024:
        return f"{size} B"
    kilobytes = size / 1024
    if kilobytes < 1000:
        return f"{kilobytes:.0f} KB"
    megabytes = kilobytes / 1024
    if megabytes < 1000:
        return f"{megabytes:.1f} MB".replace(".", ",")
    return f"{megabytes / 1024:.1f} GB".replace(".", ",")
