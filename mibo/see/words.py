"""What the person reads about seeing by picture, in the app's ten languages."""
from __future__ import annotations

LANGS = ("zh", "zh-TW", "en", "ja", "ko", "de", "es", "fr", "pt-BR", "ru")

# Settings' "always" for pictures, one plain sentence.
SWITCH = {
    "zh": "开启后，遇到只能按画面操作的窗口时，{who}将直接把该窗口的截图发送给你的模型服务 {service}，不再逐次询问；不截取其他窗口或整个屏幕。关闭后，每次发送前均会在对话中询问。",
    "zh-TW": "開啟後，遇到只能依畫面操作的視窗時，{who}會直接把該視窗的截圖傳送給你的模型服務 {service}，不再逐次詢問；不擷取其他視窗或整個螢幕。關閉後，每次傳送前都會在對話中詢問。",
    "en": "When this is on, {who} sends the picture of a window it can only work by sight to your model service, {service}, without asking each time. No other window, and never the whole screen. When it is off, the chat asks first each time.",
    "ja": "オンにすると、画面でしか操作できないウィンドウでは、{who}が毎回確認せずにそのウィンドウの画像をモデルサービス {service} に送ります。他のウィンドウや画面全体は送りません。オフのときは、送る前に毎回チャットで確認します。",
    "ko": "켜면 화면으로만 작업할 수 있는 창에서 {who}가 매번 묻지 않고 그 창의 이미지를 모델 서비스 {service}에 보냅니다. 다른 창이나 전체 화면은 보내지 않습니다. 끄면 보내기 전에 매번 채팅에서 묻습니다.",
    "de": "Ist das an, schickt {who} das Bild eines Fensters, das sich nur nach Sicht bedienen lässt, ohne jedes Mal zu fragen an deinen Modelldienst {service}. Kein anderes Fenster, nie den ganzen Bildschirm. Ist es aus, fragt der Chat jedes Mal vorher.",
    "es": "Si está activado, {who} envía la imagen de una ventana que solo puede manejar mirando a tu servicio de modelo, {service}, sin preguntar cada vez. Ninguna otra ventana, y nunca la pantalla entera. Si está desactivado, el chat pregunta antes cada vez.",
    "fr": "Activé, {who} envoie l'image d'une fenêtre qu'il ne peut utiliser qu'à vue à ton service de modèle, {service}, sans demander à chaque fois. Aucune autre fenêtre, jamais l'écran entier. Désactivé, le chat demande avant chaque envoi.",
    "pt-BR": "Com isto ligado, {who} envia a imagem de uma janela que só consegue usar pela imagem ao seu serviço de modelo, {service}, sem perguntar toda vez. Nenhuma outra janela, nunca a tela inteira. Desligado, o chat pergunta antes de cada envio.",
    "ru": "Если включено, {who} отправляет снимок окна, с которым можно работать только на глаз, вашему сервису модели {service}, не спрашивая каждый раз. Никаких других окон и никогда весь экран. Если выключено, чат каждый раз спрашивает заранее.",
}

# The card at the first picture of a task: this once, always, or no.
ASK = {
    "zh": "「{window}」只能按画面操作，是否将此窗口的截图发送给模型服务 {service}？本次任务中将由{who}根据画面点击、拖动和输入；发送内容前由自动审查核对你的原话，未通过时请你确认。",
    "zh-TW": "「{window}」只能依畫面操作，是否將此視窗的截圖傳送給模型服務 {service}？本次任務中將由{who}根據畫面點擊、拖曳和輸入；傳送內容前由自動審查核對你的原話，未通過時請你確認。",
    "en": "{window} can only be worked by its picture. Send screenshots of this window to your model service, {service}? For this task {who} will click, drag and type by looking; before anything is sent, the automatic review checks it against your own words and asks you when it does not pass.",
    "ja": "「{window}」は画面でしか操作できません。このウィンドウの画像をモデルサービス {service} に送りますか？このタスクの間、{who}は画面を見てクリック、ドラッグ、入力をします。送信の前に自動レビューがあなたの言葉と照らし合わせ、通らないときは確認します。",
    "ko": "「{window}」은(는) 화면으로만 작업할 수 있습니다. 이 창의 이미지를 모델 서비스 {service}에 보낼까요? 이 작업 동안 {who}가 화면을 보고 클릭하고 끌고 입력합니다. 보내기 전에 자동 검토가 사용자의 말과 대조하고, 통과하지 못하면 묻습니다.",
    "de": "{window} lässt sich nur nach Sicht bedienen. Bilder dieses Fensters an deinen Modelldienst {service} schicken? Für diese Aufgabe klickt, zieht und tippt {who} nach Sicht; bevor etwas gesendet wird, gleicht die automatische Prüfung es mit deinen eigenen Worten ab und fragt dich, wenn es nicht durchgeht.",
    "es": "{window} solo se puede manejar mirando su imagen. ¿Enviar capturas de esta ventana a tu servicio de modelo, {service}? En esta tarea {who} hará clic, arrastrará y escribirá mirando; antes de enviar algo, la revisión automática lo compara con tus propias palabras y te pregunta si no lo aprueba.",
    "fr": "{window} ne s'utilise qu'à vue. Envoyer des captures de cette fenêtre à ton service de modèle, {service} ? Pour cette tâche, {who} cliquera, glissera et tapera à vue ; avant tout envoi, la vérification automatique le compare à tes propres mots et te demande s'il ne passe pas.",
    "pt-BR": "{window} só pode ser usada pela imagem. Enviar capturas desta janela ao seu serviço de modelo, {service}? Nesta tarefa {who} vai clicar, arrastar e digitar olhando; antes de enviar algo, a revisão automática confere com as suas próprias palavras e pergunta a você quando não aprova.",
    "ru": "С окном «{window}» можно работать только на глаз. Отправлять его снимки вашему сервису модели {service}? В этой задаче {who} будет нажимать, перетаскивать и вводить текст на глаз; перед отправкой автоматическая проверка сверит это с вашими словами и спросит вас, если не пропустит.",
}

# The same card when the model only looks (it did not point in the self-test): nothing is clicked.
LOOK = {
    "zh": "「{window}」只能按画面操作，是否将此窗口的截图发送给模型服务 {service}？截图仅用于让{who}查看并告诉你如何操作，不会点击或输入。",
    "zh-TW": "「{window}」只能依畫面操作，是否將此視窗的截圖傳送給模型服務 {service}？截圖僅用於讓{who}查看並告訴你如何操作，不會點擊或輸入。",
    "en": "{window} can only be worked by its picture. Send a screenshot of this window to your model service, {service}? {who} will look at it and tell you what to do; it will not click or type.",
    "ja": "「{window}」は画面でしか操作できません。このウィンドウの画像をモデルサービス {service} に送りますか？{who}は画像を見て操作方法をお伝えします。クリックや入力はしません。",
    "ko": "「{window}」은(는) 화면으로만 작업할 수 있습니다. 이 창의 이미지를 모델 서비스 {service}에 보낼까요? {who}가 이미지를 보고 방법을 알려 드리며, 클릭하거나 입력하지 않습니다.",
    "de": "{window} lässt sich nur nach Sicht bedienen. Ein Bild dieses Fensters an deinen Modelldienst {service} schicken? {who} sieht es sich an und sagt dir, was zu tun ist; es klickt und tippt nicht.",
    "es": "{window} solo se puede manejar mirando su imagen. ¿Enviar una captura de esta ventana a tu servicio de modelo, {service}? {who} la mirará y te dirá qué hacer; no hará clic ni escribirá.",
    "fr": "{window} ne s'utilise qu'à vue. Envoyer une capture de cette fenêtre à ton service de modèle, {service} ? {who} la regardera et te dira quoi faire ; il ne cliquera ni ne tapera rien.",
    "pt-BR": "{window} só pode ser usada pela imagem. Enviar uma captura desta janela ao seu serviço de modelo, {service}? {who} vai olhar e dizer o que fazer; não vai clicar nem digitar.",
    "ru": "С окном «{window}» можно работать только на глаз. Отправить его снимок вашему сервису модели {service}? {who} посмотрит и скажет, что делать; нажимать и вводить ничего не будет.",
}

# The card that lets the character work by sight in one window, for one task.
LEASE = {
    "zh": "{who}无法识别「{window}」中按钮的用途：该窗口未提供按钮名称。是否允许{who}在本次任务中根据画面点击和拖动？发送内容前由自动审查核对你的原话，未通过时请你确认。",
    "zh-TW": "{who}無法辨識「{window}」中按鈕的用途：該視窗未提供按鈕名稱。是否允許{who}在本次任務中根據畫面點擊和拖曳？傳送內容前由自動審查核對你的原話，未通過時請你確認。",
    "en": "{who} can't tell what the buttons in {window} do: the window doesn't name them. Let {who} click and drag there by looking, for this task only? Before anything is sent, the automatic review checks it against your own words.",
    "ja": "{who}には「{window}」のボタンが何をするのか分かりません（ウィンドウが名前を出していません）。このタスクの間だけ、画面を見てクリックやドラッグをしてよいですか？送信の前に自動レビューがあなたの言葉と照らし合わせます。",
    "ko": "{who}는 「{window}」의 버튼이 무엇을 하는지 알 수 없습니다(창이 이름을 알려 주지 않음). 이 작업 동안만 화면을 보고 클릭하고 끌어도 될까요? 보내기 전에 자동 검토가 사용자의 말과 대조합니다.",
    "de": "{who} erkennt nicht, was die Schaltflächen in {window} tun: das Fenster benennt sie nicht. Darf {who} dort nur für diese Aufgabe nach Sicht klicken und ziehen? Bevor etwas gesendet wird, gleicht die automatische Prüfung es mit deinen eigenen Worten ab.",
    "es": "{who} no puede saber qué hacen los botones de {window}: la ventana no les da nombre. ¿Dejas que {who} haga clic y arrastre ahí mirando, solo para esta tarea? Antes de enviar algo, la revisión automática lo compara con tus propias palabras.",
    "fr": "{who} ne peut pas savoir ce que font les boutons de {window} : la fenêtre ne les nomme pas. Laisser {who} cliquer et glisser à vue, pour cette tâche seulement ? Avant tout envoi, la vérification automatique le compare à tes propres mots.",
    "pt-BR": "{who} não sabe o que fazem os botões de {window}: a janela não dá nome a eles. Deixar {who} clicar e arrastar ali olhando, só nesta tarefa? Antes de enviar algo, a revisão automática confere com as suas próprias palavras.",
    "ru": "{who} не видит, что делают кнопки в «{window}»: окно не называет их. Разрешить {who} нажимать и перетаскивать там на глаз, только в этой задаче? Перед отправкой автоматическая проверка сверит это с вашими словами.",
}

# A key that may send, pressed by sight, when the automatic review did not pass it.
KEY = {
    "zh": "{who}将在「{window}」中按 {key}。此操作基于画面识别，无法确定是否会发送内容。",
    "zh-TW": "{who}將在「{window}」中按 {key}。此操作基於畫面辨識，無法確定是否會傳送內容。",
    "en": "{who} wants to press {key} in {window}. It is working by sight and can't tell whether that sends something.",
    "ja": "{who}は「{window}」で {key} を押そうとしています。画面を見て操作しているため、何かが送信されるかは分かりません。",
    "ko": "{who}가 「{window}」에서 {key}를 누르려고 합니다. 화면을 보고 작업하므로 무언가 전송되는지 알 수 없습니다.",
    "de": "{who} möchte in {window} {key} drücken. Es arbeitet nach Sicht und weiß nicht, ob damit etwas gesendet wird.",
    "es": "{who} quiere pulsar {key} en {window}. Trabaja mirando y no puede saber si eso envía algo.",
    "fr": "{who} veut appuyer sur {key} dans {window}. Il travaille à vue et ne peut pas savoir si cela envoie quelque chose.",
    "pt-BR": "{who} quer pressionar {key} em {window}. Está trabalhando pela imagem e não sabe se isso envia algo.",
    "ru": "{who} хочет нажать {key} в «{window}». Он работает на глаз и не знает, отправит ли это что-нибудь.",
}

# A window only a picture can work, for a model that takes no pictures: which kind of model would do it.
NO_PICTURES = {
    "zh": "「{window}」只能按画面操作，而当前模型无法读取图片，已交由你操作。如需{who}操作此类窗口，请在“设置 › 模型”中选用支持图片识别的模型。",
    "zh-TW": "「{window}」只能依畫面操作，而目前的模型無法讀取圖片，已交由你操作。如需{who}操作這類視窗，請在「設定 › 模型」中選用支援圖片辨識的模型。",
    "en": "{window} can only be worked by its picture, and your model takes no pictures, so it is back to you. For {who} to work windows like this, choose a model that reads pictures in Settings › Model.",
    "ja": "「{window}」は画面でしか操作できず、今のモデルは画像を読めないため、お返しします。{who}にこうしたウィンドウを操作させるには、「設定 › モデル」で画像を読めるモデルを選んでください。",
    "ko": "「{window}」은(는) 화면으로만 작업할 수 있는데 지금 모델은 이미지를 읽지 못해 돌려드립니다. {who}가 이런 창을 다루게 하려면 「설정 › 모델」에서 이미지를 읽는 모델을 고르세요.",
    "de": "{window} lässt sich nur nach Sicht bedienen, und dein Modell nimmt keine Bilder – zurück an dich. Damit {who} solche Fenster bedient, wähle unter Einstellungen › Modell ein Modell, das Bilder liest.",
    "es": "{window} solo se puede manejar mirando y tu modelo no acepta imágenes, así que te lo devuelve. Para que {who} maneje ventanas así, elige en Ajustes › Modelo un modelo que lea imágenes.",
    "fr": "{window} ne s'utilise qu'à vue et ton modèle ne prend pas d'images : je te le rends. Pour que {who} utilise ce genre de fenêtre, choisis dans Réglages › Modèle un modèle qui lit les images.",
    "pt-BR": "{window} só pode ser usada pela imagem e seu modelo não aceita imagens, então volta para você. Para {who} usar janelas assim, escolha em Configurações › Modelo um modelo que lê imagens.",
    "ru": "С окном «{window}» можно работать только на глаз, а ваша модель не принимает картинки, поэтому возвращаю вам. Чтобы {who} работал с такими окнами, выберите в «Настройки › Модель» модель, которая читает картинки.",
}


def say(table: dict, lang: str, **fields) -> str:
    text = table.get(lang) or table.get(lang.split("-")[0]) or table["en"]
    return text.format(**fields)


def spaced(lang: str, who: str) -> str:
    """A name in Latin letters stands apart from the Chinese round it."""
    return f" {who} " if lang.startswith("zh") and who.isascii() else who
