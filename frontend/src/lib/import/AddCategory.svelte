<!--
@component
One more category, for a row that fits none: an expense under one of the groups there are or
in a group of its own, or income. Every import from now on has it.
-->
<script lang="ts">
	import { t } from '#lib/i18n/index.svelte.js';

	interface Props {
		/** The top-level expense categories it can go under. */
		under: string[];
		onadd: (name: string, side: string, group: string) => Promise<string | null>;
		/** What opens the form. */
		summary?: string;
	}

	let { under, onadd, summary = t('import.addCategorySummaryDefault') }: Props = $props();

	let name = $state('');
	let where = $state('expenses:');
	let adding = $state(false);
	let problem = $state<string | null>(null);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		adding = true;
		const [side, group] = where.split(':');
		problem = await onadd(name, side, group);
		adding = false;
		if (!problem) name = '';
	}

	const field =
		'h-8 rounded-control border border-line bg-raised px-2 text-small text-ink focus:border-brand focus:outline-none';
</script>

<details class="group rounded-panel bg-panel px-4 py-3">
	<summary class="cursor-pointer text-body text-ink-secondary marker:text-ink-muted hover:text-ink">
		{summary}
	</summary>
	<form onsubmit={submit} class="mt-3 flex flex-wrap items-end gap-3">
		<label class="flex flex-col gap-1 text-caption text-ink-secondary">
			{t('import.categoryNameLabel')}
			<input bind:value={name} required maxlength="60" class="{field} w-56" />
		</label>
		<label class="flex flex-col gap-1 text-caption text-ink-secondary">
			{t('import.goesUnderLabel')}
			<select bind:value={where} class={field}>
				<option value="expenses:">{t('import.expensesOwnGroup')}</option>
				{#each under as group (group)}
					<option value="expenses:{group}">{t('import.expensesUnderGroup', { group })}</option>
				{/each}
				<option value="income:">{t('transactions.kindIncome')}</option>
			</select>
		</label>
		<button
			type="submit"
			disabled={adding}
			class="h-8 rounded-control border border-line px-3 text-small text-ink hover:border-line-strong disabled:opacity-60"
		>
			{t(adding ? 'import.adding' : 'import.addCategoryButton')}
		</button>
		{#if problem}<p class="w-full text-small text-loss" role="alert">{problem}</p>{/if}
	</form>
</details>
