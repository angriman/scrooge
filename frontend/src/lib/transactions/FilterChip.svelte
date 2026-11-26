<!--
@component
One field of the filter as a chip: its name until it is set, then what it is set to, with a
cross that clears it. Choosing it opens what it can be set to.
-->
<script lang="ts">
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import X from '@lucide/svelte/icons/x';
	import { Popover } from 'bits-ui';
	import type { Snippet } from 'svelte';
	import { t } from '#lib/i18n/index.svelte.js';
	import { cn } from '#lib/ui/cn.js';

	interface Props {
		/** What the field is: `Account`. */
		label: string;
		/** What it is set to, written out; null when it is not set. */
		value: string | null;
		onclear: () => void;
		open?: boolean;
		/** What it can be set to; `close` closes the chip once it is. */
		children: Snippet<[close: () => void]>;
	}

	let { label, value, onclear, open = $bindable(false), children }: Props = $props();
	const close = () => (open = false);
</script>

<span
	class={cn(
		'inline-flex h-11 items-center rounded-full border text-small transition-colors sm:h-8',
		value
			? 'border-brand/40 bg-brand-soft text-ink'
			: 'border-line bg-sunken text-ink-secondary hover:border-line-strong hover:text-ink'
	)}
>
	<Popover.Root bind:open>
		<Popover.Trigger
			class={cn(
				'inline-flex h-full items-center gap-1.5 rounded-full pl-3',
				value ? 'pr-1.5' : 'pr-2.5'
			)}
		>
			{#if value}
				<span class="sr-only">{label}:</span>
				<span class="max-w-48 truncate">{value}</span>
			{:else}
				{label}
				<ChevronDown class="size-3.5" aria-hidden="true" />
			{/if}
		</Popover.Trigger>
		<Popover.Portal>
			<Popover.Content
				sideOffset={6}
				align="start"
				class="z-50 max-h-[min(28rem,var(--bits-popover-content-available-height))] w-72 overflow-y-auto rounded-panel border border-line bg-raised p-2 shadow-float"
			>
				{@render children(close)}
			</Popover.Content>
		</Popover.Portal>
	</Popover.Root>
	{#if value}
		<button
			type="button"
			onclick={onclear}
			aria-label={t('transactions.clearFilterAria', { label: label.toLowerCase() })}
			class="mr-0.5 grid size-10 place-items-center rounded-full text-ink-secondary hover:bg-raised hover:text-ink sm:mr-1 sm:size-6"
		>
			<X class="size-3.5" aria-hidden="true" />
		</button>
	{/if}
</span>
