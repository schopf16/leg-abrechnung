"""One way to write a byte count, wherever the app shows one.

Lives at the top level rather than under `app/gui/` because
`app.emailing.graph_client` renders sizes too, and the emailing layer must
not import from the GUI (see CLAUDE.md's layering rule).


There were two: the backup page rendered `"1.3 MB"` with an English
decimal point while the email page rendered `"1,3 MB"` with a German
comma, so the same quantity looked like two different numbers depending
on where it was read. The same rule that already governs percentages and
factors (`app.domain.production_capacity.format_percent`) applies here.

Both defects of the email page's version are fixed on the way: a 300-byte
file reported `"0 KB"`, and 1 MB minus one byte reported `"1024 KB"` --
both in the sentence shown immediately before an irreversible send, which
is the worst place to make a reader doubt what is attached.
"""


def format_size(num_bytes: int) -> str:
    """Render a byte count the way a German reader sizes up a file.

    Args:
        num_bytes: Size in bytes; negative values are treated as 0.

    Returns:
        Bytes below 1 KB (so a small file never reads as "0 KB"), then KB,
        then MB from 1000 KB upwards (so nothing ever reads as "1024 KB"),
        with a German decimal comma.
    """
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
