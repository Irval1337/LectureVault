import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useRef, type ReactNode } from 'react'

export function Dialog({ open, onOpenChange, title, description, children, className = '' }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; description: string; children: ReactNode; className?: string
}) {
  const returnFocus = useRef<HTMLElement | null>(null)
  const wasOpen = useRef(false)
  // Capture before Portal children can run React autoFocus.
  if (open && !wasOpen.current && typeof document !== 'undefined') returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
  wasOpen.current = open
  return <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="dialog-overlay" />
      <DialogPrimitive.Content className={`dialog-content ${className}`} onCloseAutoFocus={event => {
        const target = returnFocus.current
        const otherDialog = document.querySelector('[role="dialog"][data-state="open"]')
        if (target?.isConnected && (!otherDialog || otherDialog.contains(target))) { event.preventDefault(); target.focus({ preventScroll: true }) }
      }}>
        <DialogPrimitive.Title className="dialog-title">{title}</DialogPrimitive.Title>
        <DialogPrimitive.Description className="dialog-description">{description}</DialogPrimitive.Description>
        {children}
        <DialogPrimitive.Close className="dialog-close button button-ghost button-icon" aria-label="Закрыть"><X size={18} /></DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  </DialogPrimitive.Root>
}
