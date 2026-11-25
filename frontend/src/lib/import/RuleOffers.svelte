<!--
@component
Rules offered after rows with a rule-given category were changed by hand: each writes a rule
first in its rules file only when added, so future imports classify that merchant the same way.
-->
<script lang="ts">
	import ArrowDownLeft from '@lucide/svelte/icons/arrow-down-left';
	import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { RuleSuggestion } from '#lib/types.js';
	import Panel from '#lib/ui/Panel.svelte';

	interface Props {
		offers: RuleSuggestion[];
		unoffered: string | null;
		adding: string | null;
		added: Record<string, boolean>;
		errors: Record<string, string>;
		onadd: (offer: RuleSuggestion) => void;
	}

	let { offers, unoffered, adding, added, errors, onadd }: Props = $props();
</script>

<Panel title={t('import.rulesToAddTitle')} note={t('import.rulesToAddNote')}>
	{#if unoffered}
		<p class="text-small text-caution" role="status">{unoffered}</p>
	{/if}
	{#if offers.length}
		<ul class="divide-y divide-line">
			{#each offers as offer (offer.id)}
				<li class="py-2.5">
					<div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
						<div class="min-w-0 flex-1">
							<p class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-body text-ink">
								{#if offer.direction === 'in'}
									<ArrowDownLeft
										class="size-4 shrink-0 text-gain"
										aria-label={t('import.receivedAria')}
									/>
								{:else}
									<ArrowUpRight
										class="size-4 shrink-0 text-ink-muted"
										aria-label={t('import.paidAria')}
									/>
								{/if}
								<span class="font-medium">{offer.merchant}</span>
								<span class="text-ink-muted" aria-hidden="true">→</span>
								<span>{offer.category}</span>
							</p>
							<p class="mt-0.5 pl-6 text-caption text-ink-muted">
								{t('import.firstInFile', { file: offer.file })} ·
								<code class="font-mono text-ink-secondary">{offer.match}</code>
							</p>
						</div>
						{#if added[offer.id]}
							<span class="inline-flex items-center gap-1.5 text-small font-medium text-gain">
								<CircleCheck class="size-4" aria-hidden="true" />
								{t('import.ruleAdded')}
							</span>
						{:else}
							<button
								type="button"
								onclick={() => onadd(offer)}
								disabled={adding !== null}
								aria-label={t('import.addRuleAria', { merchant: offer.merchant })}
								class="h-8 shrink-0 rounded-control border border-line px-3 text-small font-medium text-ink hover:border-line-strong disabled:opacity-60"
							>
								{t(adding === offer.id ? 'import.adding' : 'import.addRule')}
							</button>
						{/if}
					</div>
					{#if errors[offer.id]}
						<p class="mt-1 pl-6 text-small text-loss" role="alert">{errors[offer.id]}</p>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</Panel>
