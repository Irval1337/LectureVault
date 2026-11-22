// A title suffix keeps resized illustrations valid Markdown in other readers.
export function imageDetails(alt: string, title: string) {
  const match = /(?:^| )\[width=(\d{1,3})%\]$/.exec(title)
  if (match && Number(match[1]) >= 1 && Number(match[1]) <= 999)
    return { alt, caption: title.slice(0, match.index), ratio: Number(match[1]) / 100 }
  // Earlier Crepe versions exported the resize factor in place of the alt text.
  if (/^[0-9]\.\d\d$/.test(alt) && Number(alt) > 0)
    return { alt: '', caption: title, ratio: Number(alt) }
  return { alt, caption: title, ratio: 1 }
}

export function imageTitle(caption: string, ratio: number) {
  const percent = Math.max(1, Math.min(999, Math.round(ratio * 100)))
  return percent === 100 ? caption : [caption, `[width=${percent}%]`].filter(Boolean).join(' ')
}
