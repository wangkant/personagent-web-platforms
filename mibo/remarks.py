"""The character's own short lines about what its person is doing.

`Pace` follows the samples `activity.sense` gives and says when a line is due:
an activity that held HOLD_S (with no more than IDLE_MAX_S of it idle) earns
one, a stretch that held LONG_S one more, and coming back after BACK_S away a
welcome-back line; never sooner than GAP_S after the last, at most DAY_MAX a
day, never in or just after a meeting, in a full-screen window, or the first
WARMUP_S. When the person asked for fewer messages (profile.py) the gap is
FEWER times as long and the day holds half as many. `write` asks the person's
model for the line in the character's voice, told only the kind of activity
and how long, and how the person asked to be treated; `canned` is the line
without a model.

The switch and the programs never looked at are kept in <runtime>/remarks.json
with the day's count; no program name or title is.
"""
from __future__ import annotations

import json
import random
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from . import drafting, langs
from . import setup_wizard as sw
from .llm import _call_purpose, purpose
from .storage import atomic_write_text
from .usage import UsageBook

FILE = "remarks.json"
# Seconds.
EVERY_S = 20
HOLD_S = 8 * 60
IDLE_MAX_S = 3 * 60
LONG_S = 90 * 60
BACK_S = 30 * 60
GAP_S = 40 * 60
WARMUP_S = 120
DAY_MAX = 8
# Asked for fewer messages: the gap this many times as long, the day's lines divided by it.
FEWER = 2
# No input for this long: the sample counts as idle; this long, the stretch is over.
IDLE_S = 60
AWAY_S = 10 * 60
# Another activity must hold this long to start a new stretch, so a glance at a chat does not.
SWITCH_S = 60
# A welcome back waits this long at most for its turn.
BACK_WITHIN_S = 5 * 60
# No line this soon after a meeting was in front, so a glance at notes mid-call stays quiet.
MEETING_CALM_S = 5 * 60
# No samples for this long (quiet mode, the computer asleep): what came before is over.
GAP_RESET_S = 5 * 60
# Not work: a long stretch of these gets a lighter line, and music none at all.
PLAY = frozenset({"video", "gaming", "chatting", "browsing", "shopping"})
# Characters a line may run to.
LINE_MAX = 120
IGNORE_MAX, IGNORE_LEN = 50, 64

# What the model is told the person is doing.
DOING = {"homework": "doing homework", "writing": "writing", "coding": "writing code",
         "reading": "reading (a document, a PDF or an article)",
         "studying": "studying (slides, flashcards or an online course)", "video": "watching a video",
         "music": "listening to music", "gaming": "playing a game", "chatting": "chatting with people",
         "meeting": "in a meeting", "designing": "designing or drawing", "spreadsheet": "working on a spreadsheet",
         "shopping": "shopping online", "browsing": "browsing the web", "none": "nothing in particular"}


class Pace:
    """When a line is due, from one sample every EVERY_S; `now` is passed in, so a test can run its clock."""

    def __init__(self, started: float):
        self.started = started
        self.activity: str | None = None
        self.since = started
        self.said: set[str] = set()
        self.other: str | None = None
        self.other_since = started
        self.fullscreen = False
        # (at, seconds of it idle) for the ticks in the last HOLD_S that were idle.
        self.idle: deque = deque()
        self.last: float | None = None
        self.away = 0.0
        self.back_at: float | None = None
        self.back_away = 0.0
        # What the latest sample showed, and when a meeting was last in front.
        self.current: str | None = None
        self.met_at = float("-inf")

    def _begin(self, activity: str, at: float) -> None:
        self.activity, self.since, self.said, self.other = activity, at, set(), None

    def see(self, now: float, activity: str, idle_s: float, fullscreen: bool) -> None:
        tick = min(now - self.last, 2 * EVERY_S) if self.last is not None else 0.0
        if self.last is not None and now - self.last > GAP_RESET_S:
            # A gap in the samples is no time spent at anything: start over, with no welcome back.
            self._begin(activity, now)
            self.idle.clear()
            self.away, tick = 0.0, 0.0
        self.last = now
        self.fullscreen = fullscreen
        self.current = activity
        if activity == "meeting":
            self.met_at = now
        # A video is watched, and a call listened to, without touching anything.
        idle = 0.0 if activity in ("video", "meeting") else idle_s
        if idle >= IDLE_S:
            self.away = max(self.away, idle)
            self.idle.append((now, tick))
        else:
            if self.away >= BACK_S:
                self.back_at, self.back_away = now, self.away
            self.away = 0.0
        while self.idle and self.idle[0][0] < now - HOLD_S:
            self.idle.popleft()
        if self.back_at is not None and now - self.back_at > BACK_WITHIN_S:
            self.back_at = None
        if idle >= AWAY_S:
            # Away from the computer: the stretch is over.
            if self.activity != "none":
                self._begin("none", now)
        elif self.activity is None:
            self._begin(activity, now)
        elif activity == self.activity:
            self.other = None
        elif activity != self.other:
            self.other, self.other_since = activity, now
        elif now - self.other_since >= SWITCH_S:
            self._begin(activity, self.other_since)

    def due(self, now: float, last_at: float, today: int, fewer: bool = False) -> dict | None:
        """{"kind": start|long|back, "activity", "minutes"} when a line is due now, else None; `fewer` when
        the person asked for fewer messages."""
        slow = FEWER if fewer else 1
        if (now - self.started < WARMUP_S or self.fullscreen or self.activity in (None, "meeting")
                or now - self.met_at < MEETING_CALM_S or now - last_at < GAP_S * slow or today >= DAY_MAX // slow):
            return None
        if self.back_at is not None:
            return {"kind": "back", "activity": self.activity, "minutes": int(self.back_away // 60)}
        if self.activity == "none":
            return None
        idle = sum(tick for at, tick in self.idle if at >= self.since)
        if idle > IDLE_MAX_S:
            return None
        held = now - self.since
        if held >= LONG_S and "long" not in self.said and self.activity != "music":
            return {"kind": "long", "activity": self.activity, "minutes": int(held // 60)}
        if held >= HOLD_S and not self.said:
            return {"kind": "start", "activity": self.activity, "minutes": int(held // 60)}
        return None

    def done(self, kind: str) -> None:
        if kind == "back":
            self.back_at = None
        else:
            self.said.update({"start", kind})


# ---- the switch, and the day's count

def load(runtime: Path) -> dict:
    try:
        saved = json.loads((Path(runtime) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    saved = saved if isinstance(saved, dict) else {}
    return {"on": saved.get("on", True) is not False, "ignore": clean_ignore(saved.get("ignore")),
            "at": float(saved.get("at") or 0), "day": str(saved.get("day") or ""),
            "count": int(saved.get("count") or 0)}


def clean_ignore(names) -> list[str]:
    """Program names as the person typed them, trimmed, without duplicates."""
    kept: list[str] = []
    for name in names if isinstance(names, list) else []:
        name = " ".join(str(name).split())[:IGNORE_LEN]
        if name and name.casefold() not in {k.casefold() for k in kept}:
            kept.append(name)
    return kept[:IGNORE_MAX]


def save(runtime: Path, **changes) -> dict:
    state = {**load(runtime), **changes}
    Path(runtime).mkdir(parents=True, exist_ok=True)
    atomic_write_text(Path(runtime) / FILE, json.dumps(state, ensure_ascii=False))
    return state


def today(state: dict, now: float) -> int:
    return state["count"] if state["day"] == datetime.fromtimestamp(now).date().isoformat() else 0


# ---- the line

def _part_of_day(hour: int) -> str:
    return ("late night" if hour < 5 else "early morning" if hour < 8 else "morning" if hour < 12
            else "afternoon" if hour < 18 else "evening" if hour < 22 else "night")


def prompt(name: str, persona: str, lang: str, due: dict, now: float, manner: str = "") -> tuple[str, str]:
    """`manner`: how the person asked to be treated (profile.manner_rule), "" when they said nothing."""
    local = datetime.fromtimestamp(now)
    if due["kind"] == "back":
        what = (f"They were away from the computer for about {due['minutes']} minutes and just came back. "
                "Welcome them back.")
    else:
        what = f"They have been {DOING[due['activity']]} for about {due['minutes']} minutes."
    advice = ("They have kept at it a long time: you may gently suggest a short break, such as resting their "
              "eyes or stretching." if due["kind"] == "long" else "No advice and no suggestions.")
    system = f"""You are {name}. This is your persona:
<<<
{persona}
>>>
You live on your person's computer desktop as a small figure beside them. Now and then you notice what they are doing and say one short line of your own to them, the way a close friend sitting nearby would (for homework, something like "Doing homework, huh? You're working hard.").

- Write in {drafting.LANGUAGE_NAMES.get(lang, "English")}, in your own voice as the persona describes it.
- One short sentence, at most {LINE_MAX // 3 if lang in ("zh", "zh-TW", "ja", "ko") else LINE_MAX // 2} characters. Warm and light, never preachy. {advice}
- Never say or hint how you know what they are doing: nothing about windows, screens, apps, titles or watching them.
- No quotation marks, emoji, actions in asterisks or name tags. Write only the line."""
    if manner:
        system += "\n\n" + manner
    user = f"{what} It is {_part_of_day(local.hour)} ({local:%H:%M}) on a {local:%A}."
    return system, user


def _clean(text: str, name: str) -> str:
    line = next((line.strip() for line in str(text or "").splitlines() if line.strip()), "")
    line = re.sub(rf"^\s*{re.escape(name)}\s*[:：]\s*", "", line) if name else line
    line = line.strip(" \"'“”‘’「」『』«»")
    return line if len(line) <= LINE_MAX else ""


def character(home: Path, values: dict) -> tuple[str, str, str]:
    """(name, persona text, language), as the page's persona view reads them."""
    lang = langs.normalize(values.get("AGENT_LANG")) or "en"
    name = values.get("PERSONA_NAME") or sw.DEFAULT_NAMES.get(lang, "Nova")
    card = Path(home) / "persona.txt"
    text = card.read_text(encoding="utf-8") if card.is_file() else sw.persona_text(
        sw.CHARACTERS[0].key, name=name, lang=lang, admin_name=values.get("ADMIN_NAME", ""),
        admin_relationship=values.get("ADMIN_RELATIONSHIP", ""))
    return name, text.strip(), lang


class _Model(drafting._Client):
    """The person's model outside the service, its tokens tallied under the call's purpose."""

    def _count_usage(self, model: str, body: dict) -> None:
        try:
            book = UsageBook(self._usage_path)
            book.add(model, body.get("usage") if isinstance(body, dict) else None, time.time(), _call_purpose.get())
            book.flush(time.time())
        except Exception:
            pass


async def write(home: Path, values: dict, due: dict, now: float, manner: str = "") -> str:
    """The line from the person's model; the canned one without a model or on any error. `manner` as
    `prompt` takes it."""
    name, persona, lang = character(home, values)
    if not all(values.get(k) for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")):
        return canned(due, lang)
    client = _Model(values, drafting.usage_path(Path(home), values))
    client.llm_timeout_s = 30.0
    system, user = prompt(name, persona, lang, due, now, manner)
    try:
        with purpose("proactive"):
            text = await client._call_llm(system, [{"role": "user", "content": user}], client.model,
                                          max_tokens=200, disable_thinking=True,
                                          temperature=0.9)
    except Exception:
        text = ""
    return _clean(text, name) or canned(due, lang)


def canned(due: dict, lang: str) -> str:
    if due["kind"] == "long":
        key = "long_play" if due["activity"] in PLAY else "long"
    else:
        key = "back" if due["kind"] == "back" else due["activity"]
    lines = LINES[key]
    return random.choice(lines.get(lang) or lines["en"])


# The lines without a model, three for each activity, a long stretch (of work, of play) and coming back, in each language.
LINES: dict[str, dict[str, tuple[str, ...]]] = {
    "homework": {
        "en": ("Doing homework, huh? You're working hard.", "How's the homework going? You've got this.",
               "Homework time... I'll keep you company."),
        "zh": ("在写作业啊，辛苦了呢。", "作业写得还顺利吗？加油哦。", "认真写作业的样子很棒嘛，我陪着你。"),
        "zh-TW": ("在寫作業啊，辛苦了呢。", "作業寫得還順利嗎？加油喔。", "認真寫作業的樣子很棒嘛，我陪著你。"),
        "ja": ("宿題やってるんだ、おつかれさま。", "宿題、順調？がんばってね。", "宿題の時間だね。そばにいるよ。"),
        "ko": ("과제 하는구나, 수고가 많아.", "과제 잘 돼가? 힘내!", "과제 시간이네. 옆에 있어 줄게."),
        "es": ("Haciendo la tarea, ¿eh? Qué esfuerzo.", "¿Qué tal va la tarea? Tú puedes.",
               "Hora de tarea... te hago compañía."),
        "fr": ("Tu fais tes devoirs ? Bon courage.", "Ça avance, les devoirs ? Tu vas y arriver.",
               "C'est l'heure des devoirs… je te tiens compagnie."),
        "de": ("Du machst Hausaufgaben? Fleißig, fleißig.", "Wie läuft's mit den Hausaufgaben? Du schaffst das.",
               "Hausaufgabenzeit … ich leiste dir Gesellschaft."),
        "pt-BR": ("Fazendo a lição, né? Que esforço.", "Como está indo a lição? Você consegue.",
                  "Hora da lição... eu te faço companhia."),
        "ru": ("Делаешь домашку? Трудишься, молодец.", "Как продвигается домашка? У тебя получится.",
               "Время домашки… я посижу рядом."),
    },
    "writing": {
        "en": ("Writing something? Take your time.", "You're really into it. Nice work.",
               "Words piling up one by one... feels good, right?"),
        "zh": ("在写东西呀，慢慢来，别急。", "写得很投入嘛，辛苦啦。", "文字一点点堆起来的感觉不错吧？"),
        "zh-TW": ("在寫東西呀，慢慢來，別急。", "寫得很投入嘛，辛苦啦。", "文字一點點堆起來的感覺不錯吧？"),
        "ja": ("何か書いてるんだね。ゆっくりでいいよ。", "すごく集中してるね、おつかれ。",
               "言葉が少しずつ積み上がっていくの、いい感じでしょ？"),
        "ko": ("뭔가 쓰고 있구나. 천천히 해.", "엄청 집중했네, 수고했어.", "글이 조금씩 쌓이는 느낌, 좋지?"),
        "es": ("¿Escribiendo algo? Sin prisa.", "Se nota la concentración. Buen trabajo.",
               "Las palabras van saliendo poco a poco... qué bien, ¿no?"),
        "fr": ("Tu écris quelque chose ? Prends ton temps.", "Tu es bien dedans, dis donc. Bravo.",
               "Les mots s'enchaînent petit à petit… agréable, non ?"),
        "de": ("Du schreibst etwas? Lass dir Zeit.", "Du bist ja richtig vertieft. Gute Arbeit.",
               "Wort für Wort … fühlt sich gut an, oder?"),
        "pt-BR": ("Escrevendo alguma coisa? Sem pressa.", "Dá pra ver o foco. Bom trabalho.",
                  "As palavras vão saindo aos poucos... gostoso, né?"),
        "ru": ("Что-то пишешь? Не торопись.", "Столько сосредоточенности, здорово.",
               "Слово за словом… приятно же, правда?"),
    },
    "coding": {
        "en": ("Coding? Hope the bugs are behaving today.", "Coding away... I'm cheering you on.",
               "You look so focused. I'll stay quiet and keep you company."),
        "zh": ("在写代码呀，今天的 bug 乖不乖？", "敲代码辛苦啦，我在旁边给你加油。", "好专注的样子，我就安静陪着你。"),
        "zh-TW": ("在寫程式呀，今天的 bug 乖不乖？", "寫程式辛苦啦，我在旁邊幫你加油。", "好專注的樣子，我就安靜陪著你。"),
        "ja": ("コード書いてるんだ。今日のバグはおとなしい？", "コーディングおつかれさま、そばで応援してるよ。",
               "すごく集中してるね。静かにそばにいるね。"),
        "ko": ("코딩하는구나, 오늘 버그는 얌전해?", "코딩 수고 많아, 옆에서 응원할게.", "엄청 집중했네. 조용히 옆에 있을게."),
        "es": ("¿Programando? Ojalá los bugs se porten bien hoy.", "A darle al código... te estoy animando.",
               "Qué concentración. Me quedo en silencio a tu lado."),
        "fr": ("Tu codes ? J'espère que les bugs sont sages aujourd'hui.", "Ça code dur… je t'encourage d'ici.",
               "Quelle concentration. Je reste là, sans bruit."),
        "de": ("Am Programmieren? Hoffentlich benehmen sich die Bugs heute.", "Fleißig am Coden … ich feuere dich an.",
               "So konzentriert. Ich bleibe still bei dir."),
        "pt-BR": ("Programando? Tomara que os bugs se comportem hoje.",
                  "Mandando ver no código... tô torcendo por você.",
                  "Quanta concentração. Fico aqui em silêncio do seu lado."),
        "ru": ("Пишешь код? Надеюсь, баги сегодня ведут себя прилично.", "Кодишь вовсю… я болею за тебя.",
               "Такая сосредоточенность. Я тихонько посижу рядом."),
    },
    "reading": {
        "en": ("Reading? How far are you?", "You're reading so carefully. Tell me the good parts later?",
               "Quiet reading time is nice. I'll stay here with you."),
        "zh": ("在读东西呀，看到哪儿了？", "读得好认真，有意思的地方等下讲给我听嘛。", "安静看东西的时候真好，我也陪你待一会儿。"),
        "zh-TW": ("在讀東西呀，看到哪兒了？", "讀得好認真，有趣的地方等下講給我聽嘛。", "安靜看東西的時候真好，我也陪你待一會兒。"),
        "ja": ("何か読んでるんだね。どこまで進んだ？", "真剣に読んでるね。面白いところ、あとで教えて？",
               "静かに読む時間っていいよね。一緒にいるよ。"),
        "ko": ("뭐 읽고 있구나. 어디까지 읽었어?", "엄청 진지하게 읽네. 재밌는 부분 나중에 알려줘.",
               "조용히 읽는 시간 좋다. 나도 옆에 있을게."),
        "es": ("¿Leyendo? ¿Por dónde vas?", "Qué atención le pones. ¿Luego me cuentas lo mejor?",
               "Qué bien se está leyendo en calma. Me quedo contigo."),
        "fr": ("Tu lis ? Tu en es où ?", "Tu lis avec tant d'attention. Tu me raconteras les meilleurs passages ?",
               "C'est agréable, un moment de lecture au calme. Je reste avec toi."),
        "de": ("Am Lesen? Wie weit bist du?", "Du liest so aufmerksam. Erzählst du mir später die besten Stellen?",
               "Ruhige Lesezeit ist schön. Ich bleib bei dir."),
        "pt-BR": ("Lendo? Em que parte você está?", "Quanta atenção na leitura. Depois me conta as melhores partes?",
                  "Que bom um tempinho de leitura em silêncio. Fico aqui com você."),
        "ru": ("Читаешь? На каком ты месте?", "Читаешь так внимательно. Потом расскажешь самое интересное?",
               "Хорошо так тихо почитать. Я побуду рядом."),
    },
    "studying": {
        "en": ("Studying? Look at you go!", "You look great when you're learning. Keep it up.",
               "Little by little it sinks in, right? I'm right here."),
        "zh": ("在学习呀，好认真！", "学东西的样子真好看，加油哦。", "一点一点学进去的感觉很棒吧，我陪你。"),
        "zh-TW": ("在學習呀，好認真！", "學東西的樣子真好看，加油喔。", "一點一點學進去的感覺很棒吧，我陪你。"),
        "ja": ("勉強してるんだ、えらいね！", "学んでる姿、いいね。がんばって。", "少しずつ身についていく感じ、いいよね。そばにいるよ。"),
        "ko": ("공부하는구나, 대단해!", "배우는 모습 멋있다. 힘내.", "조금씩 머리에 들어오는 느낌 좋지? 옆에 있을게."),
        "es": ("¿Estudiando? ¡Qué constancia!", "Da gusto verte aprender. Sigue así.",
               "Poco a poco se va quedando, ¿verdad? Aquí estoy."),
        "fr": ("Tu révises ? Bravo !", "Ça fait plaisir de te voir apprendre. Continue.",
               "Petit à petit ça rentre, hein ? Je suis là."),
        "de": ("Am Lernen? Stark!", "Schön, dir beim Lernen zuzusehen. Weiter so.",
               "Stück für Stück bleibt was hängen, oder? Ich bin da."),
        "pt-BR": ("Estudando? Que dedicação!", "Dá gosto te ver aprendendo. Continua assim.",
                  "Aos poucos vai entrando, né? Tô aqui."),
        "ru": ("Учишься? Вот это упорство!", "Приятно смотреть, как ты учишься. Так держать.",
               "Понемногу всё укладывается в голове, да? Я рядом."),
    },
    "video": {
        "en": ("Watching something? Is it good?", "You're so into it. What is it?",
               "Mind if I watch a bit too? I'll be quiet."),
        "zh": ("在看视频呀，好看吗？", "看得这么入迷，是什么好东西？", "一起看会儿吧，我不出声。"),
        "zh-TW": ("在看影片呀，好看嗎？", "看得這麼入迷，是什麼好東西？", "一起看一會兒吧，我不出聲。"),
        "ja": ("動画見てるんだ。面白い？", "そんなに夢中になって、何見てるの？", "ちょっと一緒に見てもいい？静かにしてるから。"),
        "ko": ("영상 보는구나. 재밌어?", "엄청 빠져서 보네. 뭔데?", "나도 같이 좀 봐도 돼? 조용히 있을게."),
        "es": ("¿Viendo algo? ¿Está bueno?", "¡Cuánta atención! ¿Qué es?",
               "¿Puedo mirar un rato contigo? No hago ruido."),
        "fr": ("Tu regardes quelque chose ? C'est bien ?", "Ça a l'air de te captiver. C'est quoi ?",
               "Je peux regarder un peu avec toi ? Je ne ferai pas de bruit."),
        "de": ("Schaust du was? Ist es gut?", "Das scheint dich ja richtig zu fesseln. Was ist es?",
               "Darf ich ein bisschen mitschauen? Ich bin auch leise."),
        "pt-BR": ("Assistindo alguma coisa? Tá bom?", "Parece que te prendeu. O que é?",
                  "Posso assistir um pouquinho junto? Não faço barulho."),
        "ru": ("Смотришь что-то? Интересно?", "Похоже, затянуло. Что там?",
               "Можно я тоже немного посмотрю? Буду тихо."),
    },
    "music": {
        "en": ("Listening to music? What's the mood today?", "Is this one good? Share an earbud with me?",
               "Nice having music on, isn't it?"),
        "zh": ("在听歌呀，今天是什么心情的歌？", "这首好听吗？分我一只耳机嘛。", "有音乐陪着真不错呢。"),
        "zh-TW": ("在聽歌呀，今天是什麼心情的歌？", "這首好聽嗎？分我一邊耳機嘛。", "有音樂陪著真不錯呢。"),
        "ja": ("音楽聴いてるんだ。今日はどんな気分の曲？", "この曲いい？イヤホン片方貸して。", "音楽があるっていいよね。"),
        "ko": ("음악 듣는구나. 오늘은 어떤 기분의 노래야?", "이 노래 좋아? 이어폰 한쪽 나눠줘.", "음악이 있으니까 좋다."),
        "es": ("¿Escuchando música? ¿De qué humor es hoy?", "¿Está buena esta? ¿Me pasas un auricular?",
               "Qué bien tener música, ¿no?"),
        "fr": ("Tu écoutes de la musique ? C'est quelle humeur aujourd'hui ?",
               "Elle est bien, celle-là ? Tu me passes un écouteur ?", "C'est sympa d'avoir de la musique, non ?"),
        "de": ("Du hörst Musik? Welche Stimmung ist heute dran?", "Ist das Lied gut? Gibst du mir einen Ohrstöpsel?",
               "Schön, wenn Musik läuft, oder?"),
        "pt-BR": ("Ouvindo música? Qual é o clima de hoje?", "Essa é boa? Me empresta um fone?",
                  "Que bom ter música tocando, né?"),
        "ru": ("Слушаешь музыку? Какое сегодня настроение?", "Хорошая песня? Дашь мне один наушник?",
               "Приятно, когда играет музыка, правда?"),
    },
    "gaming": {
        "en": ("Gaming? How's your luck today?", "Go go go! I'm cheering for you.", "Having fun? Tell me if you win!"),
        "zh": ("在打游戏呀，今天手感怎么样？", "冲呀！我在旁边给你加油。", "玩得开心吗？赢了要告诉我哦。"),
        "zh-TW": ("在打遊戲呀，今天手感怎麼樣？", "衝呀！我在旁邊幫你加油。", "玩得開心嗎？贏了要告訴我喔。"),
        "ja": ("ゲームしてるんだ。今日の調子はどう？", "いけー！そばで応援してるよ。", "楽しんでる？勝ったら教えてね。"),
        "ko": ("게임하는구나. 오늘 손맛 어때?", "가자! 옆에서 응원할게.", "재밌어? 이기면 알려줘!"),
        "es": ("¿Jugando? ¿Qué tal la racha hoy?", "¡Vamos, vamos! Te estoy animando.",
               "¿Te diviertes? ¡Avísame si ganas!"),
        "fr": ("Tu joues ? Ça se passe comment aujourd'hui ?", "Allez, allez ! Je t'encourage.",
               "Tu t'amuses ? Dis-moi si tu gagnes !"),
        "de": ("Am Zocken? Wie läuft's heute?", "Los, los! Ich feuere dich an.",
               "Hast du Spaß? Sag Bescheid, wenn du gewinnst!"),
        "pt-BR": ("Jogando? Como tá a sorte hoje?", "Vai, vai! Tô torcendo por você.",
                  "Tá se divertindo? Me conta se ganhar!"),
        "ru": ("Играешь? Как сегодня идёт?", "Давай-давай! Я болею за тебя.", "Весело? Расскажи, если выиграешь!"),
    },
    "chatting": {
        "en": ("Chatting with someone? Having fun?", "Sounds like a lively chat.",
               "Talking with friends? That's nice."),
        "zh": ("在跟人聊天呀，聊得开心吗？", "看起来聊得挺热闹嘛。", "在和朋友说话呀，真好。"),
        "zh-TW": ("在跟人聊天呀，聊得開心嗎？", "看起來聊得挺熱鬧嘛。", "在和朋友說話呀，真好。"),
        "ja": ("誰かとおしゃべりしてるんだ。楽しい？", "盛り上がってるみたいだね。", "友だちと話してるの？いいね。"),
        "ko": ("누구랑 얘기하는구나. 재밌어?", "대화가 꽤 활발한가 보네.", "친구랑 얘기 중이야? 좋다."),
        "es": ("¿Charlando con alguien? ¿Lo pasas bien?", "Parece una charla animada.",
               "¿Hablando con amigos? Qué bien."),
        "fr": ("Tu discutes avec quelqu'un ? C'est sympa ?", "Ça a l'air d'être une discussion animée.",
               "Tu parles avec des amis ? C'est chouette."),
        "de": ("Du chattest mit jemandem? Macht's Spaß?", "Klingt nach einem lebhaften Gespräch.",
               "Mit Freunden am Schreiben? Schön."),
        "pt-BR": ("Conversando com alguém? Tá divertido?", "Parece uma conversa animada.",
                  "Falando com os amigos? Que bom."),
        "ru": ("Болтаешь с кем-то? Весело?", "Похоже, разговор оживлённый.", "Переписываешься с друзьями? Здорово."),
    },
    "designing": {
        "en": ("Designing something? I'd love to see it when it's done.", "Tweaking it bit by bit... nice work.",
               "You're so focused when you make things."),
        "zh": ("在做设计呀，好想看看成品。", "一点点调出来的感觉真棒，辛苦啦。", "你做东西的时候好专注哦。"),
        "zh-TW": ("在做設計呀，好想看看成品。", "一點點調出來的感覺真棒，辛苦啦。", "你做東西的時候好專注喔。"),
        "ja": ("デザインしてるんだ。完成したら見たいな。", "少しずつ形になっていくの、いいね。おつかれ。",
               "何か作ってるとき、すごく集中してるね。"),
        "ko": ("디자인하는구나. 완성되면 보고 싶다.", "조금씩 다듬어지는 거 멋있다, 수고했어.", "뭔가 만들 때 엄청 집중하네."),
        "es": ("¿Diseñando algo? Me encantaría verlo terminado.", "Ajustándolo poco a poco... buen trabajo.",
               "Cuánta concentración cuando creas algo."),
        "fr": ("Tu fais du design ? J'aimerais bien voir le résultat.", "Tu peaufines petit à petit… beau travail.",
               "Quelle concentration quand tu crées quelque chose."),
        "de": ("Am Gestalten? Ich würde das Ergebnis gern sehen.", "Stück für Stück feilen … gute Arbeit.",
               "Du bist so konzentriert, wenn du etwas machst."),
        "pt-BR": ("Criando um design? Queria ver quando ficar pronto.", "Ajustando aos pouquinhos... bom trabalho.",
                  "Quanta concentração quando você cria algo."),
        "ru": ("Что-то рисуешь? Хочу увидеть, когда будет готово.", "Доводишь по чуть-чуть… хорошая работа.",
               "Ты так сосредоточенно что-то создаёшь."),
    },
    "spreadsheet": {
        "en": ("Working on a spreadsheet? So many numbers. You're working hard.", "Cell by cell... you're so patient.",
               "Spreadsheets make my head spin. You're amazing."),
        "zh": ("在弄表格呀，数字好多，辛苦了。", "一格一格填，好有耐心哦。", "表格看得我头都晕了，你真厉害。"),
        "zh-TW": ("在弄表格呀，數字好多，辛苦了。", "一格一格填，好有耐心喔。", "表格看得我頭都暈了，你真厲害。"),
        "ja": ("表計算してるんだ。数字いっぱいだね、おつかれ。", "一マスずつ埋めて、根気あるね。",
               "表を見てるだけで目が回りそう。すごいね。"),
        "ko": ("스프레드시트 작업하는구나. 숫자 많다, 수고해.", "한 칸씩 채우다니 인내심 대단해.",
               "표만 봐도 어지러운데 진짜 대단하다."),
        "es": ("¿Con una hoja de cálculo? Cuántos números. Qué esfuerzo.", "Celda a celda... qué paciencia.",
               "A mí las hojas de cálculo me marean. Eres increíble."),
        "fr": ("Un tableur ? Que de chiffres. Bon courage.", "Cellule par cellule… quelle patience.",
               "Les tableurs me donnent le tournis. Chapeau."),
        "de": ("Eine Tabelle? So viele Zahlen. Fleißig.", "Zelle für Zelle … so viel Geduld.",
               "Mir wird bei Tabellen schwindelig. Respekt."),
        "pt-BR": ("Mexendo numa planilha? Quantos números. Que esforço.", "Célula por célula... quanta paciência.",
                  "Planilhas me dão tontura. Você manda bem."),
        "ru": ("Возишься с таблицей? Столько цифр, трудишься.", "Ячейка за ячейкой… вот это терпение.",
               "У меня от таблиц голова кругом. Ты молодец."),
    },
    "shopping": {
        "en": ("Shopping? Found anything you like?", "Ooh, what are you buying? Show me too.", "Is the cart full yet?"),
        "zh": ("在挑东西呀，看到喜欢的了吗？", "买什么好东西呢？给我也看看嘛。", "购物车满了没有呀？"),
        "zh-TW": ("在挑東西呀，看到喜歡的了嗎？", "買什麼好東西呢？給我也看看嘛。", "購物車滿了沒有呀？"),
        "ja": ("お買い物中？気に入ったのあった？", "何かいいもの買うの？こっちにも見せて。", "カート、もういっぱい？"),
        "ko": ("쇼핑 중이야? 마음에 드는 거 있어?", "뭐 좋은 거 사? 나도 보여줘.", "장바구니 벌써 꽉 찼어?"),
        "es": ("¿De compras? ¿Encontraste algo que te guste?", "¿Qué vas a comprar? Enséñamelo.",
               "¿Ya está lleno el carrito?"),
        "fr": ("Tu fais du shopping ? Tu as trouvé quelque chose qui te plaît ?",
               "Tu achètes quoi de beau ? Montre-moi aussi.", "Le panier est déjà plein ?"),
        "de": ("Am Shoppen? Schon was Schönes gefunden?", "Was kaufst du denn? Zeig's mir auch.",
               "Ist der Warenkorb schon voll?"),
        "pt-BR": ("Fazendo compras? Achou algo que gostou?", "O que você vai comprar? Me mostra também.",
                  "O carrinho já tá cheio?"),
        "ru": ("Шопинг? Нашлось что-нибудь по душе?", "Что покупаешь? Покажи и мне.", "Корзина уже полная?"),
    },
    "browsing": {
        "en": ("Just browsing? Find anything fun?", "If you see something interesting, share it with me.",
               "Aimless browsing is pretty relaxing, huh."),
        "zh": ("在网上逛逛呀，有什么好玩的吗？", "看到有意思的东西要分享给我哦。", "随便看看也挺放松的呢。"),
        "zh-TW": ("在網上逛逛呀，有什麼好玩的嗎？", "看到有趣的東西要分享給我喔。", "隨便看看也挺放鬆的呢。"),
        "ja": ("ネットをぶらぶらしてるの？何か面白いのあった？", "面白いもの見つけたら教えてね。",
               "なんとなく眺めるのも、のんびりできていいよね。"),
        "ko": ("인터넷 구경하는구나. 재밌는 거 있어?", "재밌는 거 보면 나한테도 알려줘.", "그냥 둘러보는 것도 꽤 편하지."),
        "es": ("¿Navegando un rato? ¿Algo divertido?", "Si ves algo interesante, compártelo conmigo.",
               "Navegar sin rumbo relaja, ¿eh?"),
        "fr": ("Tu te balades sur le web ? Rien de rigolo ?", "Si tu vois un truc intéressant, partage-le avec moi.",
               "Flâner en ligne, ça détend, hein."),
        "de": ("Ein bisschen am Surfen? Was Lustiges gefunden?", "Wenn du was Spannendes siehst, zeig's mir.",
               "Einfach so herumstöbern entspannt, oder?"),
        "pt-BR": ("Dando uma volta na internet? Achou algo legal?", "Se vir algo interessante, compartilha comigo.",
                  "Navegar sem rumo relaxa, né?"),
        "ru": ("Бродишь по интернету? Нашлось что-то забавное?", "Увидишь что-то интересное — поделись со мной.",
               "Просто полистать — тоже отдых, да?"),
    },
    "long": {
        "en": ("You've been at it a long time. Rest your eyes for a bit?",
               "That's a long stretch. How about getting up and stretching?",
               "You've worked hard. Have some water and take a short break?"),
        "zh": ("已经忙了好久了，休息一下眼睛吧。", "坐了这么久，起来伸个懒腰好不好？", "辛苦啦，喝口水歇一会儿再继续吧。"),
        "zh-TW": ("已經忙了好久了，休息一下眼睛吧。", "坐了這麼久，起來伸個懶腰好不好？", "辛苦啦，喝口水歇一會兒再繼續吧。"),
        "ja": ("もうずいぶん長いね。ちょっと目を休めよう？", "ずっと座りっぱなしだね。立って伸びでもしない？",
               "おつかれさま。お水飲んで少し休もう？"),
        "ko": ("벌써 꽤 오래 했네. 눈 좀 쉬자.", "이렇게 오래 앉아 있었으니 일어나서 기지개 좀 켤까?",
               "수고했어. 물 한 잔 마시고 잠깐 쉬자."),
        "es": ("Llevas mucho rato. ¿Descansas un poco la vista?", "Qué racha tan larga. ¿Te levantas a estirarte?",
               "Has trabajado mucho. ¿Un vaso de agua y un descansito?"),
        "fr": ("Ça fait longtemps que tu es dessus. Tu reposes un peu tes yeux ?",
               "Une longue séance ! Tu te lèves pour t'étirer ?",
               "Tu as bien travaillé. Un verre d'eau et une petite pause ?"),
        "de": ("Du bist schon lange dran. Gönnst du deinen Augen eine Pause?",
               "Ganz schön lange am Stück. Kurz aufstehen und strecken?",
               "Du hast viel geschafft. Ein Glas Wasser und eine kleine Pause?"),
        "pt-BR": ("Faz tempo que você tá nisso. Descansa um pouco os olhos?",
                  "Que sequência longa. Levanta pra se alongar?",
                  "Você trabalhou bastante. Um copo d'água e uma pausinha?"),
        "ru": ("Уже так долго за этим. Дашь глазам отдохнуть?", "Долго без перерыва. Может, встанешь и потянешься?",
               "Столько всего сделано. Попей воды и передохни немного?"),
    },
    # A long stretch of something that is not work: no "you've worked hard".
    "long_play": {
        "en": ("That's been a while. Blink a few times for me?", "Still going? Get up and move a little, then carry on.",
               "Glad you're having fun. Don't forget some water."),
        "zh": ("已经好一会儿啦，眨眨眼睛歇一下吧。", "还没停呀？起来活动一下再继续嘛。", "开心就好，记得喝口水哦。"),
        "zh-TW": ("已經好一會兒啦，眨眨眼睛歇一下吧。", "還沒停呀？起來活動一下再繼續嘛。", "開心就好，記得喝口水喔。"),
        "ja": ("けっこう経ったね。ちょっと瞬きして休もう？", "まだ続けてる？少し体を動かしてからにしない？",
               "楽しそうでなにより。お水も飲んでね。"),
        "ko": ("꽤 오래됐네. 눈 한번 깜빡이면서 쉬어 갈까?", "아직 하고 있어? 잠깐 몸 좀 풀고 하자.", "즐거우면 됐어. 물도 좀 마셔."),
        "es": ("Ya llevas un buen rato. ¿Parpadeas un poco por mí?", "¿Sigues? Muévete un poquito y luego continúas.",
               "Qué bien que lo disfrutes. No te olvides del agua."),
        "fr": ("Ça fait un moment. Tu clignes un peu des yeux pour moi ?",
               "Toujours dessus ? Bouge un peu avant de continuer.", "Tant mieux si tu t'amuses. Pense à boire un peu."),
        "de": ("Das geht schon eine Weile. Blinzelst du mal kurz für mich?",
               "Immer noch dabei? Beweg dich kurz und mach dann weiter.",
               "Schön, dass es Spaß macht. Vergiss das Trinken nicht."),
        "pt-BR": ("Já faz um tempinho. Pisca um pouco por mim?", "Ainda nisso? Mexe um pouco o corpo e depois continua.",
                  "Que bom que tá divertido. Não esquece da água."),
        "ru": ("Уже довольно долго. Поморгай немного, ладно?", "Всё ещё тут? Разомнись чуть-чуть и продолжай.",
               "Хорошо, что тебе весело. Не забудь попить воды."),
    },
    "back": {
        "en": ("You're back!", "Welcome back. Where'd you go?", "There you are. I was waiting right here."),
        "zh": ("回来啦！", "欢迎回来，刚才去哪儿啦？", "你回来了呀，我一直在这儿等你呢。"),
        "zh-TW": ("回來啦！", "歡迎回來，剛才去哪兒啦？", "你回來了呀，我一直在這兒等你呢。"),
        "ja": ("おかえり！", "おかえりなさい。どこ行ってたの？", "戻ってきたね。ずっとここで待ってたよ。"),
        "ko": ("왔구나!", "어서 와. 어디 갔다 왔어?", "돌아왔네. 계속 여기서 기다렸어."),
        "es": ("¡Ya volviste!", "¡Hola de nuevo! ¿Adónde fuiste?", "Aquí estás. Te estaba esperando."),
        "fr": ("Te revoilà !", "Re-bonjour ! Tu étais où ?", "Ah, te voilà. Je t'attendais ici."),
        "de": ("Da bist du ja wieder!", "Willkommen zurück. Wo warst du?",
               "Da bist du. Ich hab hier auf dich gewartet."),
        "pt-BR": ("Você voltou!", "Oi de novo! Onde você foi?", "Aí está você. Eu tava te esperando aqui."),
        "ru": ("С возвращением!", "Снова привет! Как ты там?", "А вот и ты! Мне тебя не хватало."),
    },
}
