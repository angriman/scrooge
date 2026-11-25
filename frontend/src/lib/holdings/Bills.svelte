<!--
@component
The bills and bonds held, by when they are repaid: a line from today to the last maturity with
each bill where it falls, as tall as its face, and the list of them. It leads with the next
repayment, the money that comes back first.
-->
<script lang="ts">
	import { day, dayOfYear, daysBetween } from '#lib/format/date.js';
	import { approximate, money, short, signed, sum } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { Rung } from '#lib/types.js';
	import Panel from '#lib/ui/Panel.svelte';

	interface Props {
		ladder: Rung[];
		/** The day the bills are valued on and counted from: today. */
		from: string;
		/** Whether several people's bills are listed: each says whose it is. */
		people: boolean;
	}

	let { ladder, from, people }: Props = $props();

	const dated = $derived(ladder.filter((rung) => rung.maturity !== null));
	const first = $derived(dated.find((rung) => (rung.days ?? -1) >= 0));
	// Everything repaid on that day, in that currency: the money that comes back first.
	const next = $derived(
		first && {
			...first,
			face: sum(
				dated
					.filter((rung) => rung.maturity === first.maturity && rung.currency === first.currency)
					.map((rung) => rung.face)
			)
		}
	);
	// A line says something only with bills repaid on more than one day.
	const days = $derived(new Set(dated.map((rung) => rung.maturity)).size);
	const span = $derived(Math.max(1, ...dated.map((rung) => daysBetween(from, rung.maturity!))));
	const tallest = $derived(Math.max(1, ...dated.map((rung) => approximate(rung.face))));
	const when = (rung: Rung) =>
		rung.days === null
			? ''
			: rung.days < 0
				? t('bills.matured')
				: rung.days === 0
					? t('bills.today')
					: t('bills.inDays', { days: rung.days });
</script>

<Panel title={t('bills.title')}>
	{#if next}
		<p class="mb-2 text-body text-ink-secondary">
			{t('bills.nextBefore')}
			<span class="text-ink">{money(next.face, 0)} {next.currency}</span>
			{t('bills.nextMiddle', { date: day(next.maturity!), when: when(next) })}
		</p>
	{/if}

	{#if days > 1}
		<div class="relative mb-5 h-24 border-b border-line-strong" aria-hidden="true">
			{#each dated as rung, index (index)}
				{@const left = (Math.max(0, daysBetween(from, rung.maturity!)) / span) * 100}
				<span
					class="absolute bottom-0 w-2 -translate-x-1/2 rounded-t-[2px] bg-data-6"
					style:left="{left}%"
					style:height="{Math.max(6, (approximate(rung.face) / tallest) * 100)}%"
					title={t('bills.barTitle', {
						amount: short(rung.face),
						currency: rung.currency,
						date: dayOfYear(rung.maturity!)
					})}
				></span>
			{/each}
			<span class="absolute -bottom-5 left-0 text-caption text-ink-muted"
				>{t('bills.todayLabel')}</span
			>
			<span class="absolute right-0 -bottom-5 text-caption text-ink-muted">
				{dayOfYear(dated[dated.length - 1].maturity!)}
			</span>
		</div>
	{/if}

	<table class="mt-4 w-full text-small">
		<thead class="text-caption text-ink-muted">
			<tr>
				<th class="pb-2 text-left font-normal">{t('bills.colRepaid')}</th>
				<th class="pb-2 text-right font-normal">{t('bills.colFace')}</th>
				<th class="pb-2 text-right font-normal">{t('bills.colWorthToday')}</th>
				<th class="pb-2 text-right font-normal">{t('investments.interest')}</th>
			</tr>
		</thead>
		<tbody>
			{#each ladder as rung, index (index)}
				<tr class="border-t border-line">
					<td class="py-2">
						{rung.maturity ? day(rung.maturity) : t('bills.noMaturity')}
						<span class="block text-caption text-ink-muted">
							{when(rung)} · {rung.account}{#if people}, {rung.owner}{/if}
						</span>
					</td>
					<td class="py-2 text-right tabular-nums">
						{money(rung.face, 0)} <span class="text-caption text-ink-muted">{rung.currency}</span>
					</td>
					<td class="py-2 text-right tabular-nums">{money(rung.value_native)}</td>
					<td class="py-2 text-right tabular-nums text-gain">
						{rung.interest === null ? '–' : signed(rung.interest)}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</Panel>
