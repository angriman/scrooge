<!--
@component
An account's balance at its last month-ends as a small line, with no axis: whether it grew,
shrank or kept still over the year, read beside the figure it ends on. A reader who cannot see
it hears which way it went (`trend`); on a phone the line gives its room to the account's name,
and only the words stay. Nothing is drawn or said for fewer than two months.
-->
<script lang="ts">
	import type { Amount, Day } from '#lib/types.js';
	import { cn } from '#lib/ui/cn.js';
	import { sparkline, trend } from './holds.js';

	interface Props {
		/** Oldest first. */
		months: { day: Day; balance: Amount }[];
		/** What the balances are in. */
		currency: string;
		class?: string;
	}

	let { months, currency, class: className }: Props = $props();

	const WIDTH = 56;
	const HEIGHT = 20;
	const points = $derived(sparkline(months.map((month) => month.balance), WIDTH, HEIGHT, 2.5));
	const last = $derived(points.split(' ').at(-1)?.split(',').map(Number) ?? [0, 0]);
	const said = $derived(trend(months, currency));
</script>

{#if points}
	<svg
		viewBox="0 0 {WIDTH} {HEIGHT}"
		width={WIDTH}
		height={HEIGHT}
		class={cn('hidden shrink-0 overflow-visible sm:block', className)}
		aria-hidden="true"
	>
		<polyline
			{points}
			fill="none"
			class="stroke-ink-muted"
			stroke-width="1.25"
			stroke-linejoin="round"
			stroke-linecap="round"
		/>
		<circle cx={last[0]} cy={last[1]} r="2" class="fill-ink-secondary" />
	</svg>
{/if}
{#if said}
	<span class="sr-only">, {said}</span>
{/if}
