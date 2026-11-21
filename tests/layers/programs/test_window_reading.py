"""A program's window as the character reads it (mibo/desk/uia.py): a native text document (an edit control, with
its scroll bars) is one control holding its text, and a page drawn by Chromium (a document full of controls) is read
by its controls. The two are told apart by what UI Automation says of what the document holds, never by a program's
name. (2026-11-21: classic Notepad's text area read as a heading over a nameless box once its scroll bars' arrow
buttons were taken for a page's controls; writing and reading in Notepad failed.)"""
from __future__ import annotations

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("the app operates other programs on Windows only", allow_module_level=True)

from mibo.desk import uia, win32  # noqa: E402

WINDOW = 7


class Name:
    """A stand-in for a member of System.Windows.Automation: properties and patterns are known by their path."""

    def __init__(self, path: str = "A"):
        self.path = path

    def __getattr__(self, name: str):
        if name == "Equals":
            return lambda other: False
        return Name(f"{self.path}.{name}")

    def __call__(self, *args, **kwargs):
        return Name(f"{self.path}()")


class Kids(list):
    @property
    def Count(self) -> int:
        return len(self)


class Rect:
    def __init__(self, box):
        self.X, self.Y, self.Width, self.Height = box
        self.IsEmpty = False


class Kind:
    def __init__(self, kind: str):
        self.ProgrammaticName = f"ControlType.{kind}"


class El:
    """One element of the stand-in tree: its control type, name, box, the patterns it has and its text."""
    made = 0

    def __init__(self, kind: str, name: str = "", box=(0, 0, 10, 10), *, patterns=(), text: str = "",
                 focusable: bool = False, children=()):
        El.made += 1
        self.text = text
        self.CachedChildren = Kids(children)
        self.props = {"ControlTypeProperty": Kind(kind), "NameProperty": name, "BoundingRectangleProperty": Rect(box),
                      "ProcessIdProperty": 42, "RuntimeIdProperty": [El.made], "IsEnabledProperty": True,
                      "IsKeyboardFocusableProperty": focusable}
        for p in patterns:
            self.props[f"Is{p}PatternAvailableProperty"] = True

    def GetCachedPropertyValue(self, prop, *_):
        return self.props.get(prop.path.rsplit(".", 1)[-1])

    def GetCurrentPropertyValue(self, prop):
        return self.text

    def GetCurrentPattern(self, _pattern):
        text = self.text

        class Range:
            @staticmethod
            def GetText(_n):
                return text
        return type("Pattern", (), {"DocumentRange": Range})()

    def GetUpdatedCache(self, _cache):
        return self


@pytest.fixture
def window(monkeypatch):
    """A window of one program, process 42, whose tree the test gives; returns the look's print."""
    monkeypatch.setattr(uia, "_A", Name())
    for name, value in {"dpi_aware": lambda: None, "descendants": lambda pid: {pid}, "alive": lambda h: True,
                        "top_windows": lambda: [WINDOW], "rect": lambda h: (0, 0, 900, 600),
                        "title_of": lambda h: "Untitled - Notepad", "class_of": lambda h: "Notepad",
                        "chromium_pages": lambda h: [], "multiline": lambda h: True}.items():
        monkeypatch.setattr(win32, name, value)

    def look(*children) -> str:
        monkeypatch.setattr(uia, "element", lambda h: El("Window", "Untitled - Notepad", (0, 0, 900, 600),
                                                         children=children))
        return uia.View(WINDOW, 42).look()
    return look


def scroll_bar(name: str) -> El:
    return El("ScrollBar", name, children=[El("Button", "Back by small amount", patterns=["Invoke"]),
                                           El("Button", "Forward by small amount", patterns=["Invoke"])])


def test_an_edit_controls_document_with_scroll_bars_is_one_control_holding_its_text(window):
    shown = window(El("Document", "Text Editor", (0, 40, 900, 520), patterns=["Text", "Value", "Scroll"],
                      text="苹果\n牛奶", focusable=True,
                      children=[scroll_bar("Horizontal Scroll Bar"), scroll_bar("Vertical Scroll Bar")]),
                   El("MenuItem", "File", (0, 0, 40, 20), patterns=["ExpandCollapse"]))
    line = next(line for line in shown.splitlines() if "Text Editor" in line)
    assert line.startswith("[1] document"), shown
    assert "苹果" in line and "## Text Editor" not in shown
    assert "Back by small amount" not in shown


def test_a_page_drawn_by_chromium_is_read_by_its_controls(window):
    page = El("Document", "Lumen", (0, 0, 900, 600), patterns=["Text", "Scroll"], children=[
        El("Group", "", (0, 0, 300, 600), children=[El("ListItem", "Main agent", (10, 10, 200, 30), focusable=True,
                                                       patterns=["SelectionItem"])]),
        El("Edit", "Message", (300, 540, 500, 40), patterns=["Value"], focusable=True),
        El("Button", "Send", (820, 540, 60, 40), patterns=["Invoke"]),
        scroll_bar("Vertical Scroll Bar")])
    shown = window(El("Pane", "", (0, 0, 900, 600), children=[page]))
    assert "## Lumen" in shown and "document" not in shown
    assert 'listitem "Main agent"' in shown and 'textbox "Message"' in shown and 'button "Send"' in shown
