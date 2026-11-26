<!--
@component
Close an account, or open it again. Closing asks for a day, the last its statements cover to
begin with, and says why when the account held something that day: it is closed on a day it held
nothing. A broker's currencies are closed and opened together.
-->
<script lang="ts">
	import { day as shown } from '#lib/format/date.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { StatementsAccount } from '#lib/types.js';

	interface Props {
		account: StatementsAccount;
		/** Close it on a day; the reason it was refused, or null once it is closed. */
		onclose: (day: string) => Promise<string | null>;
		/** Open it again; the reason it was refused, or null. */
		onreopen: () => Promise<string | null>;
	}

	let { account, onclose, onreopen }: Props = $props();

	let closing = $state(false);
	let when = $state('');
	let busy = $state(false);
	let problem = $state<string | null>(null);

	function start() {
		when = account.committed ?? '';
		problem = null;
		closing = true;
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		problem = await onclose(when);
		busy = false;
	}

	async function reopen() {
		busy = true;
		problem = await onreopen();
		busy = false;
	}

	const button =
		'h-11 rounded-control border border-line px-3 text-small text-ink hover:border-line-strong disabled:opacity-60 sm:h-8';
</script>

<div class="mb-3">
	{#if account.closed}
		<p class="flex flex-wrap items-center gap-3 text-small text-ink-muted">
			{t('accounts.closedOn', { date: shown(account.closed) })}
			<button type="button" disabled={busy} onclick={reopen} class={button}
				>{t('accounts.reopen')}</button
			>
		</p>
	{:else if closing}
		<form onsubmit={submit} class="flex flex-wrap items-end gap-3">
			<label class="flex flex-col gap-1 text-caption text-ink-secondary">
				{t('accounts.closedOnLabel')}
				<input
					type="date"
					bind:value={when}
					required
					class="h-11 rounded-control border border-line bg-raised px-2 text-small text-ink focus:border-brand focus:outline-none sm:h-8"
				/>
			</label>
			<button type="submit" disabled={busy} class={button}>
				{t(busy ? 'accounts.closing' : 'accounts.closeAccount')}
			</button>
			<button type="button" disabled={busy} onclick={() => (closing = false)} class={button}>
				{t('accounts.cancel')}
			</button>
		</form>
	{:else}
		<button type="button" onclick={start} class={button}>{t('accounts.closeAccount')}</button>
	{/if}
	{#if problem}<p class="mt-2 text-small text-loss" role="alert">{problem}</p>{/if}
</div>
