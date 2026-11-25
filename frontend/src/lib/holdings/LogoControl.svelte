<!--
@component
The logo of one instrument: a PNG or SVG of the user's own is kept in place of any it had, or
the one it has is taken out. Nothing is fetched: the image comes from this browser.
-->
<script lang="ts">
	import { send } from '#lib/api.js';
	import { t } from '#lib/i18n/index.svelte.js';

	interface Props {
		symbol: string;
		/** Whether it has a logo now. */
		has: boolean;
		token: string;
		/** Called once a change is made. */
		onchange: () => void | Promise<void>;
	}

	let { symbol, has, token, onchange }: Props = $props();

	let busy = $state(false);
	let problem = $state<string | null>(null);
	let input = $state<HTMLInputElement>();

	async function answer(path: string, body: unknown) {
		busy = true;
		problem = null;
		const done = await send<{ logos: Record<string, string> }>(path, body, token);
		busy = false;
		if (!done.ok) {
			problem = (done.sent.errors ?? []).join(' ') || t('api.changeRefused');
			return;
		}
		await onchange();
	}

	async function chosen() {
		const file = input?.files?.[0];
		if (!file) return;
		const form = new FormData();
		form.append('symbol', symbol);
		form.append('file', file);
		await answer('/api/logos', form);
		if (input) input.value = '';
	}

	async function remove() {
		if (!confirm(t('holdings.logoConfirmRemove', { symbol }))) return;
		await answer('/api/logos/remove', { symbol });
	}

	const quiet = 'text-small text-ink-secondary hover:text-ink disabled:opacity-60';
</script>

<div class="mt-3 flex flex-wrap items-center gap-3 text-small">
	<span class="text-ink-muted">{t('holdings.logoLabel')}</span>
	<input
		bind:this={input}
		type="file"
		accept="image/png,image/svg+xml,.png,.svg"
		class="sr-only"
		aria-label={t('holdings.logoChooseAria', { symbol })}
		onchange={chosen}
	/>
	<button type="button" class={quiet} disabled={busy} onclick={() => input?.click()}>
		{busy ? t('settings.saving') : has ? t('holdings.logoReplace') : t('holdings.logoAdd')}
	</button>
	{#if has}
		<button type="button" class={quiet} disabled={busy} onclick={remove}>
			{t('accounts.remove')}
		</button>
	{/if}
	<span class="text-caption text-ink-muted">{t('holdings.logoHint')}</span>
	{#if problem}<p class="basis-full text-small text-loss" role="alert">{problem}</p>{/if}
</div>
