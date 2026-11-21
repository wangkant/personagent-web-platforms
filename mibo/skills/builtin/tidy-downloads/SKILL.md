---
name: tidy-downloads
description: Tidy a cluttered folder such as Downloads or the Desktop - sort its files into subfolders by kind and point out duplicates and large old files, leaving every deletion to the person. Use when they ask to clean up, tidy, organize or sort a folder.
license: Apache-2.0
metadata:
  mibo-title-zh: 整理下载文件夹
  mibo-description-zh: 将下载文件夹、桌面等杂乱的文件夹按类型归入子文件夹，并列出重复文件和占用空间较大的旧文件；是否删除由你决定。
---

# Tidying a folder

Work with use_computer, one PowerShell command at a time. The folder is the one they named; Downloads when they
said 下载 or named none.

1. Look first and change nothing. What kinds of files are there:
   `Get-ChildItem -File | Group-Object Extension | Sort-Object Count -Descending | Select-Object Count, Name`
   and the ten largest:
   `Get-ChildItem -File | Sort-Object Length -Descending | Select-Object -First 10 Name, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}, LastWriteTime`
2. Propose a plan in your reply, in a few lines: the subfolders (named in their language), how many files go to
   each, and the large old files they may no longer need. Ask whether to go ahead. Move nothing in this turn
   unless they already said to just do it.
3. When they agree, make the subfolders and move one kind per command, for example:
   `New-Item -ItemType Directory -Force -Name 'Images' | Out-Null; Get-ChildItem -File | Where-Object Extension -in '.jpg','.jpeg','.png','.gif','.webp','.heic' | Move-Item -Destination 'Images'`
   Leave files still downloading (.crdownload, .part, .tmp) and files changed in the last hour where they are.
4. Never delete. List duplicates (the same name with " (1)", or the same size and name) and large old installers,
   and let them decide. If they ask to delete, that is its own command, which waits for their press on the card.
5. End with what moved where, in two or three lines, taken from the command output rather than from the plan.

Kinds: images jpg jpeg png gif webp heic bmp svg; documents pdf doc docx xls xlsx ppt pptx txt md csv;
installers exe msi; archives zip rar 7z gz; video mp4 mov mkv avi webm; audio mp3 wav flac m4a aac.
