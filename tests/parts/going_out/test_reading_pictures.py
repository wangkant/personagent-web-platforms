"""A picture the person sends, when a vision model is set (VISION_*): it reads the picture out whole, every
text and number, and the reply model answers from that reading. A busy vision model is tried again; one that
refuses is not, and one that takes no pictures is not asked again. Through the page, against the scripted model
standing in for both."""
from __future__ import annotations

import base64
import io
import os
import struct
from pathlib import Path

import pytest
from PIL import Image

from harness.app import App, quiet_start
from harness.model import drop, fail, say, text
from harness.turn import run_turn
from mibo import transport

VISION_KEY = "sk-vision-key"
# Longer than any caption: a receipt read out line by line.
READING = "Receipt, Harbour Cafe. " + " ".join(f"Line {n}: oat latte 4.50" for n in range(1, 40)) + " Total 175.50"


@pytest.fixture
def reader(model, monkeypatch, tmp_path):
    """The app with a vision model at the scripted model's address; tries again with no wait between."""
    quiet_start(monkeypatch)
    monkeypatch.setattr(transport, "WAIT_SCALE", 0)
    served = App(Path(os.environ["AGENT_HOME"]), model, PERSONA_BROWSER_PATH=str(tmp_path / "no-browser"),
                 LLM_MAX_RETRIES="0", VISION_MODEL="scripted-vision", VISION_API_KEY=VISION_KEY,
                 VISION_BASE_URL=model.url).open()
    yield served
    served.close()


def picture(shade: int, kind: str = "PNG") -> bytes:
    out = io.BytesIO()
    Image.linear_gradient("L").point(lambda v: (v + shade) % 256).save(out, kind)
    return out.getvalue()


def attached(*pictures: bytes) -> list:
    return [{"kind": "picture", "data": base64.b64encode(data).decode()} for data in pictures]


def told(result) -> str:
    return str(result.asked()[0]["messages"][-1]["content"])


def sent_picture(request: dict) -> str:
    parts = request["body"]["messages"][0]["content"]
    return next((p["image_url"]["url"] for p in parts if p.get("type") == "image_url"), "")


def test_a_picture_is_read_out_whole_and_the_reply_answers_from_it(reader) -> None:
    shown = picture(0)
    reader.model.script(text(READING), purpose="vision")
    result = run_turn(reader, "What did I spend?", say("175.50 in all."), attachments=attached(shown))
    [asked] = reader.model.asked("vision")
    assert asked["body"]["model"] == "scripted-vision" and asked["key"] == VISION_KEY
    assert asked["body"]["max_tokens"] == 1500
    assert sent_picture(asked) == f"data:image/png;base64,{base64.b64encode(shown).decode()}"
    assert READING in told(result)


def test_a_busy_vision_model_is_tried_again_up_to_three_times(reader) -> None:
    reader.model.script(fail(503), drop(), text(READING), purpose="vision")
    assert READING in told(run_turn(reader, "Read this.", say("Read."), attachments=attached(picture(1))))
    assert len(reader.model.asked("vision")) == 3

    reader.model.script(fail(429), fail(502), fail(500), purpose="vision")
    result = run_turn(reader, "And this?", say("I could not make it out."), attachments=attached(picture(2)))
    assert len(reader.model.asked("vision")) == 6
    assert result.status == 200 and "Line 1" not in told(result)


def test_a_refusal_is_not_asked_again_and_a_model_without_eyes_is_left_alone(reader) -> None:
    reader.model.script(fail(403, {"error": {"message": "forbidden"}}), purpose="vision")
    run_turn(reader, "Read this.", say("I could not."), attachments=attached(picture(3)))
    assert len(reader.model.asked("vision")) == 1

    # The vision model says it takes no pictures. What it says without the picture is no reading of it,
    # and the next picture is not sent to it.
    reader.model.script(fail(400, {"error": {"message": "this model does not support image input"}}),
                        text("Probably a cat on a mat."), purpose="vision")
    result = run_turn(reader, "And this?", say("I could not see it."), attachments=attached(picture(4)))
    assert "cat on a mat" not in told(result)
    asked = len(reader.model.asked("vision"))
    run_turn(reader, "One more.", say("Still no."), attachments=attached(picture(5)))
    assert len(reader.model.asked("vision")) == asked


def test_an_endpoint_that_refuses_the_thinking_switch_is_asked_without_it_from_then_on(reader) -> None:
    reader.model.script(fail(400, {"error": {"message": "Unrecognized request argument supplied: thinking"}}),
                        text(READING), purpose="vision")
    assert READING in told(run_turn(reader, "Read this.", say("Read."), attachments=attached(picture(6))))
    first, again = reader.model.asked("vision")
    assert "thinking" in first["body"] and "thinking" not in again["body"]

    reader.model.script(text(READING), purpose="vision")
    run_turn(reader, "And this.", say("Read."), attachments=attached(picture(7)))
    assert "thinking" not in reader.model.asked("vision")[-1]["body"]
    assert len(reader.model.asked("vision")) == 3


def test_a_picture_is_read_once_but_a_failed_read_is_tried_again(reader) -> None:
    same = attached(picture(8))
    reader.model.script(fail(503), fail(503), fail(503), purpose="vision")
    run_turn(reader, "Read this.", say("Not now."), attachments=same)
    reader.model.script(text(READING), purpose="vision")
    assert READING in told(run_turn(reader, "Again.", say("Read."), attachments=same))
    assert READING in told(run_turn(reader, "Once more.", say("Same."), attachments=same))
    assert len(reader.model.asked("vision")) == 4


def test_a_gif_is_sent_as_its_first_frame(reader) -> None:
    reader.model.script(text(READING), purpose="vision")
    run_turn(reader, "Read this.", say("Read."), attachments=attached(picture(9, "GIF")))
    sent = sent_picture(reader.model.asked("vision")[0])
    assert sent.startswith("data:image/png;base64,")
    assert base64.b64decode(sent.split(",", 1)[1]).startswith(b"\x89PNG")


def test_a_picture_too_large_to_decode_or_too_small_to_read_is_not_sent(reader) -> None:
    # A GIF that declares 20000 by 20000 pixels in a few bytes: decoding it would take gigabytes.
    bomb = (b"GIF89a" + struct.pack("<HH", 20000, 20000) + b"\x00\x00\x00" + b",\x00\x00\x00\x00\x01\x00\x01\x00\x00"
            + b"\x02\x02D\x01\x00;" + b"\x00" * 300)
    tiny = picture(10)[:8] + b"\x00" * 50
    result = run_turn(reader, "What are these?", say("I could not read them."), attachments=attached(bomb, tiny))
    assert reader.model.asked("vision") == [] and result.status == 200
