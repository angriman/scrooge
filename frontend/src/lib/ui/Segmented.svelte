<!--
@component
One choice of a few, all in sight: a range, a breakdown. Arrow keys move between them.
-->
<script lang="ts" generics="T extends string">
	import { ToggleGroup } from 'bits-ui';
	import { cn } from './cn.js';

	interface Props {
		value: T;
		options: readonly { value: T; label: string }[];
		/** What is chosen, for a screen reader: `Range`, `Break down by`. */
		label: string;
		onchange: (value: T) => void;
		class?: string;
	}

	let { value, options, label, onchange, class: className }: Props = $props();
</script>

<ToggleGroup.Root
	type="single"
	{value}
	onValueChange={(chosen) => {
		// Choosing the chosen one again leaves it chosen: one is always.
		if (chosen) onchange(chosen as T);
	}}
	aria-label={label}
	class={cn('inline-flex rounded-control border border-line bg-sunken p-0.5', className)}
>
	{#each options as option (option.value)}
		<ToggleGroup.Item
			value={option.value}
			class="rounded-[calc(var(--radius-control)-2px)] px-2.5 py-1 text-small text-ink-secondary transition-colors hover:text-ink data-[state=on]:bg-raised data-[state=on]:text-ink"
		>
			{option.label}
		</ToggleGroup.Item>
	{/each}
</ToggleGroup.Root>
