---
name: weekly-reading-digest
description: Put together a short digest of what the person read and watched in the app's browser over the past week, grouped into a few themes, with one page worth going back to. Use when they ask what they read this week, or for a weekly review or summary of their reading.
license: Apache-2.0
metadata:
  mibo-title-zh: 每周阅读回顾
  mibo-description-zh: 将过去一周在应用内浏览器中阅读和观看的内容归纳为几个主题，并挑出一篇值得重读的内容。
---

# A week of reading

1. Call pages_they_read with n 30. Only rows from the last seven days count; each row carries its date.
2. When it returns nothing, or says sharing is off, tell them so in a sentence. Never fill in pages yourself.
3. Group the pages into two to four themes by topic. For each theme: a short name, how many pages, and one line
   on what they seemed to be after. Use the titles as they are; never invent a page or a link.
4. Pick one page worth going back to (the longest stay, or one they left early) and say why in a sentence.
5. Keep it to about eight lines. You may offer one next step as a question, such as another find on the largest
   theme; do not do it unasked.
