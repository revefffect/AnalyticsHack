import {useCallback} from 'react';
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import {Tabs, TabsList, TabsTrigger, TabsContent} from './components/tabs.jsx';
import {Dialog, DialogContent, DialogHeader, DialogTitle} from './components/dialog.jsx';
import {QualityRows, ColumnChooser} from './components/observation-tools.jsx';

export function mountObservationTools(host, section, props) {
  const root = createRoot(host);
  const Component = section === 'quality' ? QualityRows : ColumnChooser;
  flushSync(() => root.render(<Component {...props}/>));
  return () => root.unmount();
}

let activeDialog;

export function openDatasetDialog({trigger, title, mount}) {
  activeDialog?.();
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  let closing = false;
  let restoreFocus = false;
  function close(restore) {
    if (closing) return;
    closing = true;
    restoreFocus = restore;
    // Close can run inside a Radix pointer/key event.
    queueMicrotask(() => {
      root.unmount();
      container.remove();
      if (activeDialog === dispose) activeDialog = undefined;
    });
  }
  const dispose = () => close(false);
  activeDialog = dispose;
  root.render(
    <Dialog open onOpenChange={open => { if (!open) close(true); }}>
      <DialogContent aria-describedby={undefined} onCloseAutoFocus={event => {
        event.preventDefault();
        if (restoreFocus && !activeDialog && trigger?.isConnected) trigger.focus({preventScroll: true});
      }}>
        <DialogHeader>
          <DialogTitle>Настройки и доступ<span className="access-dialog-dataset"> · {title}</span></DialogTitle>
        </DialogHeader>
        <div className="access-dialog-content" ref={host => { if (host) mount(host); }} />
      </DialogContent>
    </Dialog>
  );
  return dispose;
}

function ExistingPanel({panel}) {
  const attach = useCallback(container => {
    if (container) container.append(panel);
  }, [panel]);
  return <div ref={attach} />;
}

export function mountAtlasTabs(host, {items, initialValue, onChange}) {
  const root = createRoot(host);
  const values = new Set(items.map(item => item.value));
  let value = values.has(initialValue) ? initialValue : items[0]?.value;
  let disposed = false;
  function render() {
    root.render(
      <Tabs value={value} onValueChange={select}>
        <TabsList variant="line" aria-label="Разделы атласа">
          {items.map(item => <TabsTrigger key={item.value} value={item.value}>{item.label}</TabsTrigger>)}
        </TabsList>
        {items.map(item => <TabsContent key={item.value} value={item.value}
          forceMount hidden={item.value !== value}>
          <ExistingPanel panel={item.panel} />
        </TabsContent>)}
      </Tabs>
    );
  }
  function select(nextValue) {
    if (disposed || !values.has(nextValue) || value === nextValue) return;
    value = nextValue;
    flushSync(render);
    onChange?.(value);
  }
  flushSync(render);
  return {select, dispose() {
    if (disposed) return;
    disposed = true;
    root.unmount();
  }};
}
