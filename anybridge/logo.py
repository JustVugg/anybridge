"""Terminal-native rendering of the AnyBridge wordmark.

The three masks below were traced from the approved lowercase wordmark. They
are made exclusively from Unicode quadrant blocks, so the TUI does not load or
render an image at runtime.
"""

from rich.style import Style
from rich.text import Text


WHITE = "#f4f4f4"

_WORDMARKS = {
    "large": (
        "                                           ▟",
        "                 ▄▞                      ▄▛▘                                ▗▖",
        "              ▗▟█▀                     ▗█▛                    ▄█▛         ▄█▀",
        "           ▄▄███▌           ▗     ▗▄▖ ▗██▘    ▄▄             ▐▜▀        ▗██",
        "      ▗▄▟██████▛ ▗▄▟██████▘▗█▌  ▗██▛ ▟██▙██████▀ ▟████████▘ ▟█▛ ▄▄▄▄▟█████▘▗████████▀ ▟███████▛▘",
        "      ▀▀ ▄▄▄▟██ ▄███▀▀▜██▘ ▜██ ▟██▘ ▟██▀▀▀▀███▘ ▟██▟███▛▀▘▗▟█▛ ▟██▛▀▀▀███▘▟██▀  ▐██▘▗███▟███▀▘",
        "    ▗█████▛███▘▟██▘  ▗██▘  ▝████▛ ▗▟█▛   ▗▟██▘▗█████▛▘   ▗██▛ ▟█▛   ▗▟█▛▘▟██▄▄▄▟██▘▗██▛▀▀▘ ▗▄▄▖",
        "  ▗▟██▙▄▄▄▟██▌▟██▘  ▗██▘   ▗███▘ ▗█████████▛ ▗██▀▀▜███▄ ▗██▘ ▟████████▛ ▟████████▘▗████████▛▀▘",
        "▗▟████▀▀▀▀▜▀▀ ▀     ▝▘    ▄██▀  ▐▛▀▀▀▀▀▀     ▀▘     ▀▀██▄▛  ▝▀▀▀▀▀▀     ▀▀▘  ▗██▘ ▀▀▀▀",
        "                         ▟█▛                            ▝▀▘                 ▗██▘",
        "                       ▗█▛▘                                                ▗█▛▘",
        "                      ▗▛▘                                                  ▛▘",
        "                      ▘",
    ),
    "medium": (
        "                               ▗▞",
        "           ▗▄▛                ▄▛               ▄▖       ▗▖",
        "        ▗▄▟█▌        ▗    ▗▖ ▟█    ▗          ▟▛      ▗█▛",
        "    ▗▄▟██▜█▛ ▄▟████▛▗█▖ ▄█▛▗▟█▙████▛▗██████▛▗▟█ ▄▄▄▟███▛▗██████▘▟█████▛▘",
        "    ▄▄▄▄▄██▘▟█▀▀▜█▛ ▐█▙▟█▘▗█▛▀▘ ▟█▘▗██▟█▛▀▘▗█▛ ▟█▀▀▀██▛▄█▛  ▟█▘▟███▛▀▘",
        "  ▄██▀▜▙██▚██▘ ▗█▛   ██▛ ▗██▙▄███▘▄█▛██▙▄ ▗█▛▗▟█▙▄███▘▟██████▘▟██▄▄▟█▛▘",
        "▗▟██▛▀▀▀▀▀▝▀   ▝▀  ▗██▘ ▐▀▀▀▀▀▀▘ ▝▀▘  ▝▀▜█▟▀ ▝▀▀▀▀▀▀▘ ▀▀▀▀██▘▝▀▀▀▀",
        "                  ▗█▀                     ▝▀             ▟█▘",
        "                 ▟▛▘                                    ▟▀",
        "                ▝▘",
    ),
    "small": (
        "          ▖            ▗▌",
        "       ▄▟▛            ▟▀          ▗█▘    ▄▛",
        "    ▟█▛█▛▗▄███▛▐▙ ▟▛▚█████▛▄████▛▗█▚▄▄███▛▟████▘▟████▘",
        "  ▄█████▚█▘ ▟▛ ▝██▀▗█▙▄▄█▘▟███▀ ▄█▚██▄▄█▛▟█▄▄█▘▟███▄▄",
        "▗███▀▀▀▘▀▘ ▝▀ ▗▟▛▘▐▀▀▀▀▀▘▝▀ ▝▀▜▙▛▘▀▀▀▀▀▘▝▀▀▜█▘▀▀▀▀▀▘",
        "             ▄▛▘                          ▗▛▘",
        "            ▝▘                            ▘",
    ),
}


def wordmark(text="anybridge", size="large", color=WHITE):
    """Return the wordmark as Rich text made only from terminal cells."""
    if text.lower() != "anybridge":
        raise ValueError("The traced wordmark only supports 'anybridge'")
    try:
        lines = _WORDMARKS[size]
    except KeyError as error:
        raise ValueError(f"Unknown wordmark size: {size!r}") from error

    width = max(map(len, lines))
    drawing = "\n".join(line.ljust(width) for line in lines)
    return Text(drawing, style=Style(color=color), no_wrap=True)


def width_of(text="anybridge", size="large"):
    if text.lower() != "anybridge":
        raise ValueError("The traced wordmark only supports 'anybridge'")
    return max(map(len, _WORDMARKS[size]))


def best_size(width, text="anybridge"):
    if width >= width_of(text, "large"):
        return "large"
    if width >= width_of(text, "medium"):
        return "medium"
    return "small"


if __name__ == "__main__":
    from rich.console import Console

    console = Console()
    console.print(wordmark(size=best_size(console.width)))
