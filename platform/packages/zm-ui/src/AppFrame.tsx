import { ActionIcon, AppShell, Avatar, Badge, Group, Indicator, Menu, NavLink, Text, Tooltip, UnstyledButton } from "@mantine/core";
import { useLocalStorage } from "@mantine/hooks";
import { PanelLeftClose, PanelLeftOpen, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { AppearanceMenu } from "./AppearanceMenu";

export type NavItem = { key: string; label: string; icon?: LucideIcon; badge?: number };

const initial = (name: string) => name.trim().charAt(0).toUpperCase() || "?";

/** The shell frame: 64 px top bar and a 232/72 px collapsible sidebar built from the release's nav. */
export function AppFrame({
  clientName,
  nav,
  activeKey,
  onNavigate,
  user,
  userMenu,
  children,
}: {
  clientName: string;
  nav: NavItem[];
  activeKey?: string;
  onNavigate?: (key: string) => void;
  user?: { name: string; email?: string };
  /** Menu.Item elements for the account menu (sign out, …). */
  userMenu?: ReactNode;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useLocalStorage({
    key: "zm:sidebar-collapsed",
    defaultValue: false,
    getInitialValueInEffect: false,
  });

  return (
    // ponytail: breakpoint 0 keeps the rail on phones too; add an overlay drawer if 72 px proves too wide at 375 px.
    <AppShell header={{ height: 64 }} navbar={{ width: collapsed ? 72 : 232, breakpoint: 0 }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <ActionIcon
              variant="subtle"
              color="gray"
              size="lg"
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-expanded={!collapsed}
              aria-controls="zm-sidebar"
              onClick={() => setCollapsed((c) => !c)}
            >
              {collapsed ? <PanelLeftOpen size={18} strokeWidth={1.5} aria-hidden /> : <PanelLeftClose size={18} strokeWidth={1.5} aria-hidden />}
            </ActionIcon>
            <Avatar variant="filled" radius="sm" size="sm" aria-hidden>
              {initial(clientName)}
            </Avatar>
            <Text fw={650} truncate>
              {clientName}
            </Text>
          </Group>
          <Group gap="xs" wrap="nowrap">
            <AppearanceMenu />
            {user && (
              <Menu position="bottom-end" shadow="md">
                <Menu.Target>
                  <UnstyledButton aria-label={`Account: ${user.name}`}>
                    <Avatar name={user.name} color="initials" radius="xl" size="md" />
                  </UnstyledButton>
                </Menu.Target>
                <Menu.Dropdown>
                  <Menu.Label>{user.email ?? user.name}</Menu.Label>
                  {userMenu}
                </Menu.Dropdown>
              </Menu>
            )}
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar id="zm-sidebar" aria-label="Main" p="xs">
        {nav.map((item) => (
          <SidebarItem
            key={item.key}
            item={item}
            active={item.key === activeKey}
            collapsed={collapsed}
            onClick={() => onNavigate?.(item.key)}
          />
        ))}
      </AppShell.Navbar>

      <AppShell.Main>{children}</AppShell.Main>
    </AppShell>
  );
}

function SidebarItem({ item, active, collapsed, onClick }: { item: NavItem; active: boolean; collapsed: boolean; onClick: () => void }) {
  const Icon = item.icon;
  const count = item.badge ?? 0;
  const icon = Icon ? <Icon size={18} strokeWidth={1.5} aria-hidden /> : null;

  if (!collapsed) {
    return (
      <NavLink
        component="button"
        type="button"
        label={item.label}
        leftSection={icon}
        rightSection={count > 0 ? <Badge size="sm" variant="light" circle>{count}</Badge> : null}
        active={active}
        aria-current={active ? "page" : undefined}
        onClick={onClick}
      />
    );
  }

  return (
    <Tooltip label={item.label} position="right" withArrow>
      <NavLink
        component="button"
        type="button"
        aria-label={count > 0 ? `${item.label}, ${count} open` : item.label}
        leftSection={
          <Indicator disabled={count === 0} size={8} offset={2}>
            {icon ?? <Text size="sm">{initial(item.label)}</Text>}
          </Indicator>
        }
        active={active}
        aria-current={active ? "page" : undefined}
        onClick={onClick}
      />
    </Tooltip>
  );
}
