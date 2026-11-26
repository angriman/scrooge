<!--
@component
One choice of many in a menu: a currency. Typing a letter jumps to it.
-->
<script lang="ts">
	import Check from '@lucide/svelte/icons/check';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import { Select } from 'bits-ui';
	import { cn } from './cn.js';

	interface Props {
		value: string;
		options: readonly string[];
		/** What is chosen, for a screen reader: `Currency`. */
		label: string;
		onchange: (value: string) => void;
		class?: string;
	}

	let { value, options, label, onchange, class: className }: Props = $props();

	const items = $derived(options.map((option) => ({ value: option, label: option })));
</script>

<Select.Root type="single" {value} {items} onValueChange={(chosen) => chosen && onchange(chosen)}>
	<Select.Trigger
		aria-label={label}
		class={cn(
			'inline-flex h-11 items-center gap-1.5 rounded-control border border-line bg-sunken px-2.5 text-small text-ink hover:border-line-strong sm:h-8',
			className
		)}
	>
		{value}
		<ChevronDown class="size-3.5 text-ink-secondary" aria-hidden="true" />
	</Select.Trigger>
	<Select.Portal>
		<Select.Content
			sideOffset={4}
			class="z-50 min-w-(--bits-select-anchor-width) rounded-control border border-line bg-raised p-1 shadow-float data-[state=open]:animate-in data-[state=open]:fade-in-0"
		>
			<Select.Viewport>
				{#each items as item (item.value)}
					<Select.Item
						value={item.value}
						label={item.label}
						class="flex min-h-11 cursor-default items-center justify-between gap-3 rounded-[calc(var(--radius-control)-2px)] px-2 py-2.5 text-small text-ink outline-none data-highlighted:bg-brand-soft sm:min-h-0 sm:py-1"
					>
						{#snippet children({ selected })}
							{item.label}
							{#if selected}<Check class="size-3.5 text-brand" aria-hidden="true" />{/if}
						{/snippet}
					</Select.Item>
				{/each}
			</Select.Viewport>
		</Select.Content>
	</Select.Portal>
</Select.Root>
