"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Building2,
  FileText,
  LogOut,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
} from "lucide-react";

import { useAuth } from "@/components/auth-provider";
import { ThreadRow } from "@/components/app-shell/thread-row";
import { ConfirmDialog, useConfirmTarget } from "@/components/confirm-dialog";
import { Logo } from "@/components/logo";
import { TrenchMark } from "@/components/trench-mark";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { signOutEverywhere } from "@/lib/auth-service";
import { useDeleteThread, useThreads } from "@/hooks/use-threads";
import type { ThreadSummary } from "@/lib/threads";
import { cn } from "cn";

interface NavLinkProps {
  href: string;
  label: string;
  icon: React.ReactNode;
  active: boolean;
  collapsed: boolean;
  onNavigate?: () => void;
}

function NavLink({ href, label, icon, active, collapsed, onNavigate }: NavLinkProps) {
  const button = (
    <Button
      variant="ghost"
      className={cn(
        "w-full gap-2",
        collapsed ? "justify-center px-0" : "justify-start",
        active && "bg-muted text-foreground"
      )}
      nativeButton={false}
      render={
        <Link href={href} onClick={onNavigate} aria-label={collapsed ? label : undefined}>
          {icon}
          {!collapsed && label}
        </Link>
      }
    />
  );

  if (!collapsed) return button;
  return (
    <Tooltip>
      <TooltipTrigger render={button} />
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

interface AppSidebarContentProps {
  onNavigate?: () => void;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

export function AppSidebarContent({
  onNavigate,
  collapsed = false,
  onToggleCollapse,
}: AppSidebarContentProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, setUser } = useAuth();
  const { data: threads, isLoading } = useThreads();
  const deleteThread = useDeleteThread();
  const deleteTarget = useConfirmTarget<ThreadSummary>();

  const handleNewChat = () => {
    // Deliberately does NOT create a thread here (mirrors ChatGPT): a
    // thread is only created once the user submits their first message
    // (see chat/page.tsx) -- otherwise clicking "New chat" repeatedly
    // would litter the sidebar with empty, title-less conversations.
    router.push("/chat");
    onNavigate?.();
  };

  const handleLogout = async () => {
    await signOutEverywhere();
    setUser(null);
    router.replace("/signin");
  };

  const handleConfirmDelete = () => {
    if (!deleteTarget.target) return;
    const threadId = deleteTarget.target.id;
    deleteThread.mutate(threadId, {
      onSuccess: () => {
        deleteTarget.clear();
        if (pathname === `/chat/${threadId}`) {
          router.push("/chat");
        }
      },
    });
  };

  const newChatButton = (
    <Button
      className={cn(
        "w-full gap-2",
        collapsed ? "justify-center px-0" : "justify-start"
      )}
      onClick={handleNewChat}
      aria-label={collapsed ? "New chat" : undefined}
    >
      <MessageSquarePlus className="size-4 shrink-0" />
      {!collapsed && "New chat"}
    </Button>
  );

  return (
    // min-w-0: this is a flex item inside <aside> (app-shell.tsx), and a
    // flex item's default min-width is "auto", not 0 -- meaning unwrapped
    // content anywhere inside (a long thread title, an untruncated email)
    // would otherwise force this whole column wider than the sidebar's
    // fixed w-64/w-16, spilling its background/content past the border
    // and over the main content area instead of actually wrapping/
    // truncating within it.
    <div className="flex h-full w-full min-w-0 flex-col gap-4 p-3">
      <div className={cn("flex items-center", collapsed ? "justify-center" : "justify-between")}>
        <Link href="/home" className={cn("min-w-0 px-1 py-1", collapsed && "px-0")}>
          {collapsed ? <TrenchMark className="size-6" /> : <Logo />}
        </Link>
        {onToggleCollapse && !collapsed && (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Collapse sidebar"
            onClick={onToggleCollapse}
          >
            <PanelLeftClose className="size-4" />
          </Button>
        )}
      </div>

      {collapsed ? (
        <Tooltip>
          <TooltipTrigger render={newChatButton} />
          <TooltipContent side="right">New chat</TooltipContent>
        </Tooltip>
      ) : (
        newChatButton
      )}

      <nav className="flex flex-col gap-1">
        <NavLink
          href="/documents"
          label="Documents"
          icon={<FileText className="size-4 shrink-0" />}
          active={pathname === "/documents"}
          collapsed={collapsed}
          onNavigate={onNavigate}
        />
        <NavLink
          href="/organization"
          label="Organization"
          icon={<Building2 className="size-4 shrink-0" />}
          active={pathname === "/organization"}
          collapsed={collapsed}
          onNavigate={onNavigate}
        />
      </nav>

      {!collapsed && (
        <div className="flex min-h-0 flex-1 flex-col gap-1">
          <p className="px-1 text-xs font-medium text-muted-foreground">Conversations</p>
          <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
            {isLoading &&
              Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-8 w-full rounded-lg" />
              ))}

            {!isLoading && threads?.length === 0 && (
              <p className="px-1 py-2 text-sm text-muted-foreground">
                No conversations yet.
              </p>
            )}

            {threads?.map((thread) => (
              <ThreadRow
                key={thread.id}
                thread={thread}
                active={pathname === `/chat/${thread.id}`}
                onNavigate={onNavigate}
                onRequestDelete={deleteTarget.request}
              />
            ))}
          </div>
        </div>
      )}
      {collapsed && <div className="flex-1" />}

      {collapsed && onToggleCollapse && (
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                className="mx-auto"
                aria-label="Expand sidebar"
                onClick={onToggleCollapse}
              >
                <PanelLeftOpen className="size-4" />
              </Button>
            }
          />
          <TooltipContent side="right">Expand sidebar</TooltipContent>
        </Tooltip>
      )}

      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <button
              className={cn(
                "flex w-full min-w-0 items-center gap-2 rounded-lg p-1.5 text-left hover:bg-muted",
                collapsed && "justify-center"
              )}
              aria-label={collapsed ? user?.email : undefined}
            >
              <Avatar size="sm">
                <AvatarFallback>{user?.email?.[0]?.toUpperCase() ?? "?"}</AvatarFallback>
              </Avatar>
              {!collapsed && (
                <span className="min-w-0 flex-1 truncate text-sm">{user?.email}</span>
              )}
            </button>
          }
        />
        <DropdownMenuContent align="start" className="w-56">
          <DropdownMenuItem
            onClick={() => {
              router.push("/settings");
              onNavigate?.();
            }}
          >
            <Settings className="size-4" />
            Settings
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onClick={handleLogout}>
            <LogOut className="size-4" />
            Log out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <ConfirmDialog
        open={deleteTarget.open}
        onOpenChange={(open) => !open && deleteTarget.clear()}
        title="Delete conversation?"
        description="This permanently deletes this conversation and its messages. This can't be undone."
        confirmLabel="Delete"
        isPending={deleteThread.isPending}
        onConfirm={handleConfirmDelete}
      />
    </div>
  );
}
