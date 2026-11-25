<!--
@component
What each statement says its accounts were worth at the end of its period, next to what is
worked out here for the same accounts and day. Folded away unless asked for (`#checks`).
-->
<script lang="ts">
	import { day } from '#lib/format/date.js';
	import { approximate, money } from '#lib/format/number.js';
	import { t, type MessageKey } from '#lib/i18n/index.svelte.js';
	import type { Check } from '#lib/types.js';
	import { cn } from '#lib/ui/cn.js';

	const CHECK_LINE_KEYS: Readonly<Record<string, MessageKey>> = {
		Cash: 'checks.lineCash',
		Stock: 'checks.lineStock',
		Bonds: 'checks.lineBonds'
	};
	const lineLabel = (label: string) =>
		label in CHECK_LINE_KEYS ? t(CHECK_LINE_KEYS[label]) : label;

	let {
		checks,
		open = false,
		single
	}: { checks: Check[]; open?: boolean; single: boolean } = $props();

	const differs = (check: Check) =>
		check.difference !== null && approximate(check.difference) !== 0;
	const differing = $derived(checks.filter(differs).length);
</script>

<details id="checks" {open} class="group scroll-mt-20 rounded-panel bg-panel">
	<summary class="flex cursor-pointer list-none items-center justify-between gap-4 px-4 py-3">
		<span>
			<span class="text-title font-medium text-ink">{t('checks.title')}</span>
			<span class="block text-small text-ink-secondary">
				{#if differing}
					{t('checks.differSummary', { differing, total: checks.length })}
				{:else}
					{t('checks.agreeSummary', { total: checks.length })}
				{/if}
			</span>
		</span>
		<span class="text-small text-brand group-open:hidden">{t('checks.show')}</span>
		<span class="hidden text-small text-brand group-open:inline">{t('checks.hide')}</span>
	</summary>
	<div class="overflow-x-auto px-4 pb-4">
		<table class="w-full text-body">
			<thead class="text-caption text-ink-muted">
				<tr>
					<th class="pb-2 text-left font-normal">{t('checks.colDay')}</th>
					{#if !single}<th class="pb-2 text-left font-normal">{t('checks.colPerson')}</th>{/if}
					<th class="pb-2 text-left font-normal">{t('checks.colAccounts')}</th>
					<th class="pb-2 text-left font-normal">{t('checks.colLine')}</th>
					<th class="pb-2 text-right font-normal">{t('checks.colStatementSays')}</th>
					<th class="pb-2 text-right font-normal">{t('checks.colWorkedOut')}</th>
					<th class="pb-2 text-right font-normal">{t('checks.colDifference')}</th>
				</tr>
			</thead>
			<tbody>
				{#each [...checks].reverse() as check, index (index)}
					<tr class="border-t border-line">
						<td class="py-2 whitespace-nowrap text-ink-secondary">{day(check.day)}</td>
						{#if !single}<td class="py-2">{check.owner}</td>{/if}
						<td class="py-2">{check.accounts}</td>
						<td class="py-2 text-ink-secondary">{lineLabel(check.label)}</td>
						<td class="py-2 text-right whitespace-nowrap">
							{money(check.printed)}
							<span class="text-caption text-ink-muted">{check.currency}</span>
						</td>
						<td class="py-2 text-right whitespace-nowrap">
							{#if check.derived === null}<span class="text-ink-muted"
									>{t('checks.notCounted')}</span
								>{:else}{money(check.derived)}{/if}
						</td>
						<td
							class={cn(
								'py-2 text-right whitespace-nowrap',
								differs(check) ? 'text-caution' : 'text-ink-muted'
							)}
						>
							{check.difference === null ? '' : money(check.difference)}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</details>
