// Adapted from the MIT-licensed shadcn/ui new-york-v4 dialog registry item.
// https://ui.shadcn.com/r/styles/new-york-v4/dialog.json
import * as DialogPrimitive from '@radix-ui/react-dialog';

export function Dialog(props) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />;
}

export function DialogContent({children, showCloseButton = true, ...props}) {
  return <DialogPrimitive.Portal data-slot="dialog-portal">
    <DialogPrimitive.Overlay data-slot="dialog-overlay" className="access-dialog-overlay" />
    <DialogPrimitive.Content data-slot="dialog-content" className="access-dialog-panel" {...props}>
      {children}
      {showCloseButton && <DialogPrimitive.Close data-slot="dialog-close"
        className="access-dialog-close" aria-label="Закрыть">
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" fill="none"
          stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
          <path d="m6 6 12 12M18 6 6 18" />
        </svg>
      </DialogPrimitive.Close>}
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>;
}

export function DialogHeader(props) {
  return <div data-slot="dialog-header" className="access-dialog-heading" {...props} />;
}

export function DialogTitle(props) {
  return <DialogPrimitive.Title data-slot="dialog-title" className="access-dialog-title" {...props} />;
}
