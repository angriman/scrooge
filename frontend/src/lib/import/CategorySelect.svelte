<!--
@component
The categories a row can be given, under their headings, as the device's own menu: typing a
letter jumps to it, and a phone shows its own picker.
-->
<script lang="ts">
	import type { Headed } from '#lib/types.js';
	import { cn } from '#lib/ui/cn.js';

	interface Props {
		value: string;
		leaves: Headed[];
		/** What it chooses for, for a screen reader: `Category for Landlord AG`. */
		label: string;
		/** Shown while nothing is chosen. */
		placeholder?: string;
		onchange?: (value: string) => void;
		class?: string;
	}

	let {
		value = $bindable(),
		leaves,
		label,
		placeholder = 'Choose a category',
		onchange,
		class: className
	}: Props = $props();
</script>

<select
	bind:value
	onchange={() => onchange?.(value)}
	aria-label={label}
	class={cn(
		'h-8 max-w-full min-w-0 rounded-control border bg-raised px-2 text-small focus:border-brand focus:outline-none',
		value ? 'border-brand/50 text-ink' : 'border-line text-ink-secondary',
		className
	)}
>
	<option value="">{placeholder}</option>
	{#each leaves as group (group.heading)}
		<optgroup label={group.heading}>
			{#each group.names as name (name)}
				<option value={name}>{name}</option>
			{/each}
		</optgroup>
	{/each}
</select>
