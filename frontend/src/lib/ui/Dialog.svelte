<!--
@component
A modal dialog for small edits that do not need the page behind them (a transaction's category,
renaming a category, a typed balance). Centered on desktop, a bottom sheet on a phone.
-->
<script lang="ts">
	import X from '@lucide/svelte/icons/x';
	import { Dialog as BitsDialog } from 'bits-ui';
	import type { Snippet } from 'svelte';
	import { t } from '#lib/i18n/index.svelte.js';
	import { cn } from './cn.js';

	interface Props {
		/** Whether the dialog is open; bindable or controlled with `onclose`. */
		open?: boolean;
		/** Heading of the dialog, also its accessible name. */
		title: string;
		/** Optional short explanation under the title. */
		description?: string;
		/** Whether Escape, the close button, and clicking outside dismiss it (false while saving). */
		dismissible?: boolean;
		/** Called when the dialog is dismissed. */
		onclose?: () => void;
		/** Optional element to return focus to when the dialog closes. */
		returnFocus?: () => HTMLElement | null | undefined;
		children: Snippet;
		class?: string;
	}

	let {
		open = $bindable(false),
		title,
		description,
		dismissible = true,
		onclose,
		returnFocus,
		children,
		class: className
	}: Props = $props();

	let contentRef = $state<HTMLElement | null>(null);

	function handleOpenAutoFocus(event: Event) {
		if (!contentRef) return;
		const target =
			contentRef.querySelector<HTMLElement>('[autofocus]:not([disabled])') ??
			contentRef.querySelector<HTMLElement>(
				'input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled])'
			);
		if (!target) return;
		event.preventDefault();
		target.focus({ preventScroll: true });
	}

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
			if (!next && !dismissible) return;
			open = next;
			if (!next) onclose?.();
		}
	}
>
	<BitsDialog.Portal>
		<BitsDialog.Overlay
			class="fixed inset-0 z-50 bg-sunken/70 backdrop-blur-[2px] data-[state=closed]:animate-out data-[state=closed]:duration-150 data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:duration-150 data-[state=open]:fade-in-0"
		/>
		<BitsDialog.Content
			bind:ref={contentRef}
			trapFocus={true}
			preventScroll={true}
			escapeKeydownBehavior={dismissible ? 'close' : 'ignore'}
			interactOutsideBehavior={dismissible ? 'close' : 'ignore'}
			onOpenAutoFocus={handleOpenAutoFocus}
			onCloseAutoFocus={handleCloseAutoFocus}
			class={cn(
				'fixed inset-x-0 bottom-0 z-50 flex max-h-[90dvh] w-full flex-col rounded-t-lead border border-b-0 border-line bg-panel shadow-float outline-none',
				'sm:inset-auto sm:top-1/2 sm:left-1/2 sm:max-h-[85dvh] sm:w-[calc(100vw-2rem)] sm:max-w-md sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-lead sm:border-b',
				'data-[state=open]:animate-in data-[state=open]:duration-200 data-[state=open]:slide-in-from-bottom sm:data-[state=open]:fade-in-0 sm:data-[state=open]:zoom-in-95 sm:data-[state=open]:slide-in-from-bottom-0',
				'data-[state=closed]:animate-out data-[state=closed]:duration-150 data-[state=closed]:slide-out-to-bottom sm:data-[state=closed]:fade-out-0 sm:data-[state=closed]:zoom-out-95 sm:data-[state=closed]:slide-out-to-bottom-0',
				className
			)}
		>
			<header
				class="flex shrink-0 items-start justify-between gap-3 border-b border-line px-5 py-4"
			>
				<div class="min-w-0">
					<BitsDialog.Title class="text-title font-medium text-ink">
						{title}
					</BitsDialog.Title>
					{#if description}
						<BitsDialog.Description class="mt-0.5 text-small text-ink-secondary">
							{description}
						</BitsDialog.Description>
					{/if}
				</div>
				<BitsDialog.Close
					disabled={!dismissible}
					aria-label={t('ui.close')}
					title={t('ui.close')}
					class="inline-flex size-8 shrink-0 items-center justify-center rounded-control text-ink-secondary hover:bg-sunken hover:text-ink dark:hover:bg-raised disabled:opacity-40"
				>
					<X class="size-4" aria-hidden="true" />
				</BitsDialog.Close>
			</header>
			<div
				class="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pt-4 pb-[calc(1.25rem+env(safe-area-inset-bottom))] sm:pb-5"
			>
				{@render children()}
			</div>
		</BitsDialog.Content>
	</BitsDialog.Portal>
</BitsDialog.Root>
