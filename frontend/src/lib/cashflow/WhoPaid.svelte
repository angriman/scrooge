<!--
@component
Who paid: each person's share of what the household spent over the range, and each category
split between them, the largest first. Shown for both people together; what the two paid each
other is not spending of the household, and is left out.
-->
<script lang="ts">
	import { inOrder } from '#lib/design/data.js';
	import { money, percent } from '#lib/format/number.js';
	import type { PaidBy } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import { split, type Part } from './paidBy.js';

	interface Props {
		paidBy: PaidBy;
		currency: string;
	}

	let { paidBy, currency }: Props = $props();

	const names = $derived(paidBy.people.map((person) => person.name));
	const colors = $derived(inOrder(names));
	const whole = $derived(
		split(names, Object.fromEntries(paidBy.people.map((person) => [person.name, person.spent])))
	);
	/** What a bar says to whoever cannot see it: each person and what they spent. */
	const said = (parts: Part[]) => parts.map((part) => `${part.name} ${money(part.spent, 0)}`).join(', ');
</script>

{#snippet bar(parts: Part[], label: string)}
	<span class="flex h-1.5 overflow-hidden rounded-mark bg-sunken" role="img" aria-label={label} title={label}>
		{#each parts as part (part.name)}
			<span class="block h-full" style:width="{part.width}%" style:background-color={colors.get(part.name)}></span>
		{/each}
	</span>
{/snippet}

<Panel title="Who paid" note="Each person's share of what the household spent.">
	<ul class="grid gap-x-6 gap-y-2 sm:grid-cols-2">
		{#each paidBy.people as person (person.name)}
			<li class="flex items-baseline gap-2">
				<span class="size-2 shrink-0 self-center rounded-[2px]" style:background={colors.get(person.name)}></span>
				<span class="min-w-0 truncate text-body text-ink">{person.name}</span>
				<Amount value={person.spent} {currency} places={0} class="ml-auto text-title text-ink" />
				<span class="w-12 text-right text-small text-ink-secondary tabular-nums">
					{person.share === null ? '–' : percent(person.share, 0)}
				</span>
			</li>
		{/each}
	</ul>
	<div class="mt-3">{@render bar(whole, said(whole))}</div>

	<ul class="mt-4 divide-y divide-line">
		{#each paidBy.categories as category (category.name)}
			{@const parts = split(names, category.by)}
			<li class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-1 py-2">
				<span class="truncate text-body text-ink">{category.name}</span>
				<Amount value={category.spent} places={0} class="text-right text-body text-ink" />
				<span class="col-span-2">{@render bar(parts, `${category.name}: ${said(parts)}`)}</span>
				<span class="col-span-2 flex flex-wrap gap-x-3 text-caption text-ink-muted" aria-hidden="true">
					{#each parts as part (part.name)}
						<span>{part.name} <Amount value={part.spent} places={0} class="text-ink-secondary" /></span>
					{/each}
				</span>
			</li>
		{/each}
	</ul>
</Panel>
