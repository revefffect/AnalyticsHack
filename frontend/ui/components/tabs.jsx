// Adapted from the MIT-licensed shadcn/ui new-york-v4 tabs registry item.
// https://ui.shadcn.com/r/styles/new-york-v4/tabs.json
import * as TabsPrimitive from '@radix-ui/react-tabs';

export function Tabs({orientation = 'horizontal', ...props}) {
  return <TabsPrimitive.Root data-slot="tabs" data-orientation={orientation}
    orientation={orientation} {...props} />;
}

export function TabsList({variant = 'default', ...props}) {
  return <TabsPrimitive.List data-slot="tabs-list" data-variant={variant} {...props} />;
}

export function TabsTrigger(props) {
  return <TabsPrimitive.Trigger data-slot="tabs-trigger" {...props} />;
}

export function TabsContent(props) {
  return <TabsPrimitive.Content data-slot="tabs-content" {...props} />;
}
