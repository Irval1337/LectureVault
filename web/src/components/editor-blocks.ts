import type { Node as ProseNode } from '@milkdown/kit/prose/model'
import type { Ctx } from '@milkdown/kit/ctx'
import { NodeSelection } from '@milkdown/kit/prose/state'
import { dropPoint } from '@milkdown/kit/prose/transform'
import { type EditorView } from '@milkdown/kit/prose/view'
import { imageBlockSchema } from '@milkdown/kit/component/image-block'
import { imageDetails, imageTitle } from '../lib/image-size'

export function preserveImageMetadata(ctx: Ctx) {
  ctx.update(imageBlockSchema.key, previous => context => {
    const schema = previous(context)
    return {
      ...schema,
      attrs: { ...schema.attrs, alt: { default: '', validate: 'string' } },
      parseMarkdown: {
        ...schema.parseMarkdown,
        runner(state, node, type) {
          state.addNode(type, { src: node.url, ...imageDetails(String(node.alt ?? ''), String(node.title ?? '')) })
        },
      },
      toMarkdown: {
        ...schema.toMarkdown,
        runner(state, node) {
          const { src, alt, caption, ratio } = node.attrs
          state.openNode('paragraph')
          state.addNode('image', undefined, undefined, { url: src, alt, title: imageTitle(caption, ratio) })
          state.closeNode()
        },
      },
    }
  })
}

const imageHostSelector = '.milkdown-image-block, .milkdown-image-inline'

export function picturePosition(view: EditorView, host: Element) {
  if (!host.parentNode || !view.dom.contains(host)) return
  const index = Array.prototype.indexOf.call(host.parentNode.childNodes, host)
  const pos = view.posAtDOM(host.parentNode, index)
  const node = view.state.doc.nodeAt(pos)
  if (node && pictureDetails(node) && view.nodeDOM(pos) === host) return { node, pos }
}

export function blockDragPreview(view: EditorView) {
  const doc = view.dom.ownerDocument
  let preview: HTMLElement | undefined
  function clear() { preview?.remove(); preview = undefined }
  function showPreview(event: DragEvent) {
    const block = view.dragging?.slice.content.firstChild
    if (!block || !event.dataTransfer) return
    clear()
    preview = doc.createElement('div')
    preview.className = 'block-drag-preview'
    const labels: Record<string, string> = { 'image-block': 'Изображение', table: 'Таблица', code_block: 'Блок кода', paragraph: 'Абзац', bullet_list: 'Список' }
    preview.textContent = (block.attrs.alt || block.attrs.caption || block.textContent || labels[block.type.name] || 'Блок').slice(0, 80)
    preview.setAttribute('aria-hidden', 'true')
    doc.body.append(preview)
    event.dataTransfer.setDragImage(preview, 12, 12)
    requestAnimationFrame(clear)
  }
  let source: Element | undefined
  const indicator = doc.createElement('div')
  indicator.className = 'picture-drop-indicator'
  indicator.hidden = true
  doc.body.append(indicator)

  function finish() {
    if (source) view.dragging = null
    source = undefined
    indicator.hidden = true
    clear()
  }
  function startPicture(event: DragEvent) {
    const target = event.target
    if (!(target instanceof Element) || !event.dataTransfer ||
        target.closest('input, select, button, .operation, .picture-grip')) return
    const host = target.closest(imageHostSelector)
    if (!host) return
    const picture = picturePosition(view, host)
    if (!picture?.node.attrs.src) return

    // Native image drags can carry a File even after clearData(). Claim the
    // gesture before either Crepe's upload or its table node view sees it.
    const selection = NodeSelection.create(view.state.doc, picture.pos)
    const slice = selection.content()
    const { dom, text } = view.serializeForClipboard(slice)
    event.dataTransfer.clearData()
    event.dataTransfer.setData('text/html', dom.innerHTML)
    event.dataTransfer.setData('text/plain', text)
    event.dataTransfer.effectAllowed = 'move'
    source = host
    const dragging = { slice, move: true, node: selection }
    view.dragging = dragging
    showPreview(event)
    event.stopImmediatePropagation()
  }
  function pictureTarget(event: DragEvent) {
    if (!source || !view.dragging) return
    const picture = picturePosition(view, source)
    const hit = view.posAtCoords({ left: event.clientX, top: event.clientY })
    if (!picture || !hit) return
    let target = hit.pos
    const $hit = view.state.doc.resolve(target)
    if (picture.node.isBlock && $hit.parent.isTextblock) {
      const paragraph = view.nodeDOM($hit.before())
      if (paragraph instanceof Element) {
        const rect = paragraph.getBoundingClientRect()
        target = event.clientY < rect.top + rect.height / 2 ? $hit.before() : $hit.after()
      }
    }
    const pos = dropPoint(view.state.doc, target, view.dragging.slice)
    if (pos == null || pos >= picture.pos && pos <= picture.pos + picture.node.nodeSize) return
    return { picture, pos }
  }
  function pictureEvent(event: DragEvent) {
    if (!source || !event.dataTransfer) return
    event.preventDefault()
    event.stopImmediatePropagation()
    event.dataTransfer.dropEffect = 'move'
    const target = pictureTarget(event)
    if (event.type === 'drop') {
      if (target) {
        const { picture, pos } = target
        const tr = view.state.tr.delete(picture.pos, picture.pos + picture.node.nodeSize)
        const insert = tr.mapping.map(pos)
        tr.replaceRangeWith(insert, insert, picture.node)
        // replaceRangeWith can fit the node into a new paragraph at a boundary.
        // Dispatch only when the original image has a valid destination.
        if (tr.steps.length > 1) {
          const placed = tr.doc.nodeAt(insert)
          if (placed?.eq(picture.node)) tr.setSelection(NodeSelection.create(tr.doc, insert))
          view.dispatch(tr.setMeta('uiEvent', 'drop'))
          view.focus()
        }
      }
      finish()
      return
    }
    indicator.hidden = !target
    if (target) {
      const coords = view.coordsAtPos(target.pos)
      const block = target.picture.node.isBlock
      const rect = view.dom.getBoundingClientRect()
      Object.assign(indicator.style, {
        left: (block ? rect.left : coords.left) + 'px', top: coords.top + 'px',
        width: (block ? rect.width : 2) + 'px', height: (block ? 2 : coords.bottom - coords.top) + 'px',
      })
    }
  }
  function handlePreview(event: DragEvent) {
    if (event.target instanceof Element && event.target.closest('.milkdown-block-handle') && view.dom.dataset.dragging === 'true') showPreview(event)
  }
  function allowMove(event: DragEvent) {
    if (!view.dragging?.move || !event.dataTransfer) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }
  // The handle lives in the margin. Route internal drops in that narrow strip
  // through the editor so Crepe uses the same insertion point and transaction.
  function gutter(event: DragEvent) {
    if (!view.dragging?.move || !event.dataTransfer || !(event.target instanceof Element) || view.dom.contains(event.target)) return
    const rect = view.dom.getBoundingClientRect()
    if (event.clientY < rect.top || event.clientY >= rect.bottom ||
        event.clientX < rect.left - 60 || event.clientX >= rect.left ||
        !event.target.closest('main, .milkdown-block-handle')) return
    const forwarded = new DragEvent(event.type, { bubbles: true, cancelable: true, dataTransfer: event.dataTransfer,
      clientX: rect.left + 3, clientY: event.clientY })
    view.dom.dispatchEvent(forwarded)
    if (forwarded.defaultPrevented) {
      event.preventDefault()
      event.dataTransfer.dropEffect = 'move'
    }
  }
  const gutterEvents = ['dragenter', 'dragover', 'drop'] as const
  for (const type of gutterEvents) doc.addEventListener(type, gutter)
  view.dom.addEventListener('dragover', allowMove)
  view.dom.addEventListener('dragenter', allowMove)
  view.dom.addEventListener('dragstart', startPicture, true)
  for (const type of gutterEvents) view.dom.addEventListener(type, pictureEvent, true)
  doc.addEventListener('dragstart', handlePreview)
  doc.addEventListener('dragend', finish, true)
  return () => {
    clear()
    for (const type of gutterEvents) doc.removeEventListener(type, gutter)
    view.dom.removeEventListener('dragover', allowMove)
    view.dom.removeEventListener('dragenter', allowMove)
    view.dom.removeEventListener('dragstart', startPicture, true)
    for (const type of gutterEvents) view.dom.removeEventListener(type, pictureEvent, true)
    doc.removeEventListener('dragstart', handlePreview)
    doc.removeEventListener('dragend', finish, true)
    indicator.remove()
  }
}

export function pictureDetails(node: ProseNode) {
  if (node.type.name === 'image-block') return { name: node.attrs.alt || node.attrs.caption, ratio: Number(node.attrs.ratio) }
  if (node.type.name === 'image') {
    const { alt, caption, ratio } = imageDetails(node.attrs.alt, node.attrs.title)
    return { name: alt || caption, ratio }
  }
}

export function sizedPicture(node: ProseNode, ratio: number) {
  if (node.type.name === 'image-block') return { ...node.attrs, ratio }
  const details = imageDetails(node.attrs.alt, node.attrs.title)
  return { ...node.attrs, alt: details.alt, title: imageTitle(details.caption, ratio) }
}

