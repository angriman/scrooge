<!--
@component
Details for one item (a holding, account, category or merchant): slides in from the right on
desktop while keeping the list behind it visible and clickable, and takes the full screen on a
phone. Controlled by the caller (typically from a URL query parameter via `lib/view.ts`).
-->
<script lang="ts">
	import X from '@lucide/svelte/icons/x';
	import { Dialog as BitsDialog } from 'bits-ui';
	import type { Snippet } from 'svelte';
	import { t } from '#lib/i18n/index.svelte.js';
	import { cn } from './cn.js';

	interface Props {
		/** Whether the panel is open. */
		open: boolean;
		/** Heading of the panel, also its accessible name. */
		title: string;
		/** Optional short description shown under the title. */
		description?: string;
		/** Identity of the shown item; changing it while open moves focus into the panel. */
		subject?: string | null;
		/** Optional mark or logo shown before the title. */
		leading?: Snippet;
		/** Optional metadata line shown under the title. */
		meta?: Snippet;
		/** Called when the user closes the panel (Escape or the close button). */
		onclose: () => void;
		/** Element to return focus to when the panel closes (e.g. the active row). */
		returnFocus?: () => HTMLElement | null | undefined;
		children: Snippet;
		class?: string;
	}

	let {
		open,
		title,
		description,
		subject = null,
		leading,
		meta,
		onclose,
		returnFocus,
		children,
		class: className
	}: Props = $props();

	let contentRef = $state<HTMLElement | null>(null);
	let bodyRef = $state<HTMLElement | null>(null);
	let lastSubject: string | null | undefined = undefined;
	let mobile = $state(false);

	// Only the full-screen phone sheet is modal; on desktop the list behind stays reachable.
	() => {
		if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
		const mql = window.matchMedia('(max-width: 767px)');
		mobile = mql.matches;
		const onChange = (event: MediaQueryListEvent) => {
			mobile = event.matches;
		};
		mql.addEventListener('change', onChange);
		return () => mql.removeEventListener('change', onChange);
	};

	// When switching directly from one row to another while the panel stays open, reset scroll
	// and move focus into the panel without scrolling the list behind it.
	$effect(() => {
		if (!open) {
			lastSubject = undefined;
			return;
		}
		const current = subject;
		if (lastSubject !== undefined && current !== lastSubject) {
			bodyRef?.scrollTo({ top: 0 });
			contentRef?.focus({ preventScroll: true });
		}
		lastSubject = current;
	});

	function handleCloseAutoFocus(event: Event) {
		const target = returnFocus?.();
		if (target && target.isConnected) {
			event.preventDefault();
			target.focus({ preventScroll: true });
		}
	}
</script>

<BitsDialog.Root
	bind:open={
		() => open,
		(next) => {
			if (!next && open) onclose();
		}
	}
>
	<BitsDialog.Portal>
		<BitsDialog.Content
			bind:ref={contentRef}
			preventScroll={mobile}
			trapFocus={mobile}
			interactOutsideBehavior="ignore"
			escapeKeydownBehavior="close"
			onCloseAutoFocus={handleCloseAutoFocus}
			class={cn(
				'fixed inset-0 z-[45] flex flex-col bg-panel outline-none md:inset-y-0 md:right-0 md:left-auto md:w-[min(36rem,46vw)] md:border-l md:border-line md:shadow-float',
				'data-[state=open]:animate-in data-[state=open]:duration-200 data-[state=open]:fade-in-0 md:data-[state=open]:slide-in-from-right',
				'data-[state=closed]:animate-out data-[state=closed]:duration-150 data-[state=closed]:fade-out-0 md:data-[state=closed]:slide-out-to-right',
				className
			)}
		>
			{#snippet child({ props })}
				<div {...props} bind:this={contentRef} aria-modal={mobile ? 'true' : undefined}>
					<header
						class="flex shrink-0 items-start justify-between gap-3 border-b border-line px-5 py-4"
					>
						<div class="flex min-w-0 items-center gap-3">
							{@render leading?.()}
							<div class="min-w-0">
								<BitsDialog.Title class="truncate text-heading font-medium text-ink">
									{title}
								</BitsDialog.Title>
								{#if description}
									<BitsDialog.Description class="mt-0.5 text-small text-ink-secondary">
										{description}
									</BitsDialog.Description>
								{/if}
								{#if meta}
									<div class="mt-0.5 text-small text-ink-muted">
										{@render meta()}
									</div>
								{/if}
							</div>
						</div>
						<BitsDialog.Close
							aria-label={t('ui.close')}
							title={t('ui.close')}
							class="inline-flex size-8 shrink-0 items-center justify-center rounded-control text-ink-secondary hover:bg-sunken hover:text-ink dark:hover:bg-raised"
						>
							<X class="size-4" aria-hidden="true" />
						</BitsDialog.Close>
					</header>
					<div
						bind:this={bodyRef}
						class="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pt-4 pb-[calc(1.5rem+env(safe-area-inset-bottom))]"
					>
						{@render children()}
					</div>
				</div>
			{/snippet}
		</BitsDialog.Content>
	</BitsDialog.Portal>
</BitsDialog.Root>
