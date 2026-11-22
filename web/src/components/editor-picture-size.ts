import type { EditorView } from '@milkdown/kit/prose/view'
import { pictureDetails, picturePosition, sizedPicture } from './editor-blocks'

function baseWidth(img: HTMLImageElement) {
  const container = img.closest<HTMLElement>('.milkdown-image-block, p, td, th')
  return Math.min(img.naturalWidth, container?.clientWidth || img.naturalWidth)
}

function paint(img: HTMLImageElement, ratio: number) {
  const base = baseWidth(img)
  if (!base || !img.naturalWidth) return
  img.style.width = Math.round(base * ratio) + 'px'
  img.style.height = 'auto'
}

function imageControls(view: EditorView, host: HTMLElement, img: HTMLImageElement) {
  const group = document.createElement('span')
  group.className = 'picture-size-controls'
  group.contentEditable = 'false'
  const select = document.createElement('select')
  select.className = 'picture-scale'
  group.append(select)

  function commit(percent: number) {
    const picture = picturePosition(view, host)
    if (!picture || pictureDetails(picture.node)?.ratio === percent / 100) return
    view.dispatch(view.state.tr.setNodeMarkup(picture.pos, undefined, sizedPicture(picture.node, percent / 100)))
  }
  select.onchange = () => commit(Number(select.value))
  // A native select must receive its own pointer and keyboard events inside
  // the editable DOM (including arrow keys, Space, and Tab).
  for (const type of ['pointerdown', 'mousedown', 'keydown', 'click']) {
    select.addEventListener(type, event => event.stopPropagation())
  }
  for (const side of ['left', 'right']) {
    const grip = document.createElement('span')
    grip.className = 'picture-grip grip-' + side
    grip.setAttribute('aria-hidden', 'true')
    grip.title = 'Потяните, чтобы изменить размер'
    grip.onpointerdown = event => {
      if (event.button !== 0) return
      event.preventDefault()
      event.stopPropagation()
      const base = baseWidth(img)
      if (!base) return
      const { width, height } = img.getBoundingClientRect()
      const startX = event.clientX, startY = event.clientY
      const direction = side === 'left' ? -1 : 1
      const centered = host.classList.contains('milkdown-image-block')
      let percent = Math.round(width / base * 100)
      grip.setPointerCapture(event.pointerId)
      group.classList.add('resizing')
      grip.onpointermove = move => {
        const horizontal = (move.clientX - startX) * direction * (centered ? 2 : 1)
        const vertical = (move.clientY - startY) * width / height
        const delta = Math.abs(move.clientX - startX) >= Math.abs(move.clientY - startY) ? horizontal : vertical
        percent = Math.max(1, Math.min(100, Math.round((width + delta) / base * 100)))
        paint(img, percent / 100)
      }
      grip.onpointerup = () => {
        commit(percent)
        if (grip.hasPointerCapture(event.pointerId)) grip.releasePointerCapture(event.pointerId)
      }
      grip.onlostpointercapture = () => {
        group.classList.remove('resizing')
        grip.onpointermove = grip.onpointerup = grip.onlostpointercapture = null
        const picture = picturePosition(view, host)
        if (picture) paint(img, pictureDetails(picture.node)!.ratio)
      }
    }
    group.append(grip)
  }
  img.parentElement!.append(group)
  return select
}

export function editorPictureSizes(view: EditorView) {
  let frame = 0
  let lastWidth = 0
  function render() {
    view.state.doc.descendants((node, pos) => {
      const details = pictureDetails(node)
      if (!details) return
      const host = view.nodeDOM(pos)
      if (!(host instanceof HTMLElement)) return
      const img = host.querySelector('img')
      if (!img?.naturalWidth) return
      if (node.type.name === 'image-block') img.alt = node.attrs.alt || node.attrs.caption
      const select = host.querySelector<HTMLSelectElement>('.picture-scale') ?? imageControls(view, host, img)
      const percent = Math.round(details.ratio * 100)
      select.setAttribute('aria-label', 'Размер изображения ' + (details.name || 'без подписи'))
      if (select.value !== String(percent)) {
        const sizes = [...new Set([25, 50, 75, 100, percent])].sort((a,b) => a - b)
        select.replaceChildren(...sizes.map(size => new Option(size + '%', String(size), false, size === percent)))
      }
      if (!host.querySelector('.resizing')) paint(img, details.ratio)
    })
  }
  function schedule() { cancelAnimationFrame(frame); frame = requestAnimationFrame(render) }
  const observer = new ResizeObserver(entries => {
    const width = entries[0].contentRect.width
    if (lastWidth !== width) { lastWidth = width; schedule() }
  })
  observer.observe(view.dom)
  view.dom.addEventListener('load', schedule, true)
  schedule()
  return { schedule, destroy() { observer.disconnect(); view.dom.removeEventListener('load', schedule, true); cancelAnimationFrame(frame) } }
}
