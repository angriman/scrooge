<!--
@component
Modal dialog to change a committed transaction's category, with an optional checkbox to remember
the choice as a rule for future imports.
-->
<script lang="ts">
	import { day } from '#lib/format/date.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { Headed, Transaction } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Dialog from '#lib/ui/Dialog.svelte';

	interface Props {
		/** The transaction being edited, or null when closed. */
		row: Transaction | null;
		/** The leaf categories a row can be given, under their headings. */
		leaves: Headed[];
		/** Whether a category chosen can be remembered as a rule. */
		rules: boolean;
		/** Element to return focus to when the dialog closes. */
		returnFocus?: () => HTMLElement | null | undefined;
		onclose: () => void;
		onsave: (row: Transaction, category: string, remember: boolean) => Promise<void>;
	}

	let { row, leaves, rules, returnFocus, onclose, onsave }: Props = $props();

	let displayed = $state<Transaction | null>(null);
	let category = $state('');
	let remember = $state(false);
	let saving = $state(false);
	let problem = $state<string | null>(null);

	$effect(() => {
		if (row) {
			displayed = row;
			category = row.category ?? '';
			remember = false;
			problem = null;
		}
	});

	const shown = $derived(row ?? displayed);
	const who = $derived(shown ? shown.counterparty || shown.description : '');

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (!row || !category) return;
		if (category === row.category && !remember) {
			onclose();
			return;
		}
		saving = true;
		problem = null;
		try {
			await onsave(row, category, remember);
			onclose();
		} catch (error) {
			problem = error instanceof Error ? error.message : String(error);
		} finally {
			saving = false;
		}
	}
</script>

<Dialog
	open={Boolean(row)}
	title={t('transactions.changeCategoryDialogTitle')}
	description={shown
		? t('transactions.changeCategoryDialogMeta', {
				date: day(shown.date),
				account: shown.account
			})
		: undefined}
	dismissible={!saving}
	{onclose}
	{returnFocus}
>
	{#if shown}
		<div class="mb-4 flex items-baseline justify-between gap-3 rounded-control bg-sunken px-3 py-2">
			<span class="min-w-0 truncate text-body font-medium text-ink">{who}</span>
			<Amount value={shown.amount} currency={shown.currency} class="shrink-0 text-body text-ink" />
		</div>

		<form onsubmit={submit} class="space-y-4">
			<label class="block space-y-1.5 text-small text-ink-secondary">
				<span>{t('transactions.chipCategory')}</span>
				<!-- svelte-ignore a11y_autofocus -->
				<select
					bind:value={category}
					autofocus
					disabled={saving}
					class="h-11 w-full rounded-control border border-line bg-raised px-2.5 text-body text-ink focus:border-brand focus:outline-none disabled:opacity-60 sm:h-9"
				>
					{#each leaves as group (group.heading)}
						<optgroup label={group.heading}>
							{#each group.names as name (name)}
								<option value={name}>{name}</option>
							{/each}
						</optgroup>
					{/each}
				</select>
			</label>

			{#if rules && who.trim()}
				<label
					class="flex min-h-11 items-center gap-2.5 text-small text-ink sm:min-h-0 sm:items-start"
				>
					<input
						type="checkbox"
						bind:checked={remember}
						disabled={saving}
						class="size-4 shrink-0 accent-brand sm:mt-0.5"
					/>
					<span>{t('transactions.rememberFor', { merchant: who.trim() })}</span>
				</label>
			{/if}

			{#if problem}
				<p class="text-small text-loss" role="alert">{problem}</p>
			{/if}

			<div class="flex justify-end gap-2 pt-1">
				<button
					type="button"
					onclick={onclose}
					disabled={saving}
					class="h-11 rounded-control px-3 text-small text-ink-secondary hover:text-ink disabled:opacity-60 sm:h-8"
				>
					{t('accounts.cancel')}
				</button>
				<button
					type="submit"
					disabled={saving || !category}
					class="h-11 rounded-control bg-brand px-3 text-small font-medium text-on-brand hover:bg-brand-hover disabled:opacity-60 sm:h-8"
				>
					{t(saving ? 'transactions.savingCategory' : 'transactions.saveCategory')}
				</button>
			</div>
		</form>
	{/if}
</Dialog>
