<!--
@component
What an account held at the end of a day where no statement says it, typed by hand as
`scrooge balance` and `scrooge position` take it: its balance, or how many of one instrument
it held. A figure typed can be removed again the same way, by its day (and symbol).
-->
<script lang="ts">
	import { errorMessage, send } from '#lib/api.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import Panel from '#lib/ui/Panel.svelte';

	interface Props {
		/** Whose accounts figures can be typed for. */
		owners: string[];
		/** The person shown, when one is. */
		owner: string | null;
		/** The accounts there are, to choose from or type a new one. */
		accounts: string[];
		currencies: string[];
		today: string;
		token: string;
		/** Called once a figure is typed or removed. */
		ondone: () => void | Promise<void>;
	}

	let { owners, owner, accounts, currencies, today, token, ondone }: Props = $props();

	let what = $state<'balance' | 'position'>('balance');
	let whose = $state('');
	let account = $state('');
	let day = $state('');
	let figure = $state('');
	let currency = $state('');
	let symbol = $state('');
	let tab = $state('');
	let busy = $state(false);
	let problem = $state<string | null>(null);
	let done = $state<string | null>(null);

	const person = $derived(whose || (owner && owners.includes(owner) ? owner : owners[0]) || '');

	async function submit(remove: boolean) {
		busy = true;
		problem = null;
		done = null;
		const body = {
			owner: person,
			account,
			day,
			what,
			remove,
			symbol,
			currency,
			...(what === 'balance' ? { amount: figure } : { quantity: figure }),
			...(tab ? { tab } : {})
		};
		const answer = await send<{ symbol: string | null }>('/api/accounts/typed', body, token);
		busy = false;
		if (!answer.ok) {
			problem = errorMessage(answer);
			return;
		}
		done = remove ? t('typed.removed', { account, day }) : t('typed.kept', { account, day });
		figure = '';
		await ondone();
	}

	function typeIt(event: SubmitEvent) {
		event.preventDefault();
		void submit(false);
	}

	const field =
		'h-11 rounded-control border border-line bg-raised px-2 text-small text-ink focus:border-brand focus:outline-none sm:h-8';
	const label = 'flex flex-col gap-1 text-caption text-ink-secondary';
</script>

<Panel title={t('typed.title')} note={t('typed.note')}>
	<form onsubmit={typeIt} class="flex flex-wrap items-end gap-3">
		<label class={label}>
			{t('typed.whatLabel')}
			<select bind:value={what} class={field}>
				<option value="balance">{t('typed.balance')}</option>
				<option value="position">{t('typed.position')}</option>
			</select>
		</label>
		{#if owners.length > 1}
			<label class={label}>
				{t('typed.ownerLabel')}
				<select
					value={person}
					onchange={(event) => (whose = event.currentTarget.value)}
					class={field}
				>
					{#each owners as name (name)}<option value={name}>{name}</option>{/each}
				</select>
			</label>
		{/if}
		<label class={label}>
			{t('investments.colAccount')}
			<input
				bind:value={account}
				required
				list="typed-accounts"
				maxlength="80"
				class="{field} w-48"
			/>
			<datalist id="typed-accounts">
				{#each accounts as name (name)}<option value={name}></option>{/each}
			</datalist>
		</label>
		<label class={label}>
			{t('typed.dayLabel')}
			<input bind:value={day} required type="date" max={today} class={field} />
		</label>
		{#if what === 'position'}
			<label class={label}>
				{t('typed.symbolLabel')}
				<input
					bind:value={symbol}
					required
					maxlength="20"
					placeholder="NESN.SW"
					spellcheck="false"
					class="{field} w-28 font-mono uppercase"
				/>
			</label>
		{/if}
		<label class={label}>
			{what === 'balance' ? t('typed.amountLabel') : t('holdings.colQuantity')}
			<input
				bind:value={figure}
				inputmode="decimal"
				placeholder={what === 'balance' ? '1250.50' : '10'}
				class="{field} w-32 tabular-nums"
			/>
		</label>
		<label class={label}>
			{t('typed.currencyLabel')}
			<input
				bind:value={currency}
				list="typed-currencies"
				maxlength="3"
				placeholder="CHF"
				class="{field} w-20 uppercase"
			/>
			<datalist id="typed-currencies">
				{#each currencies as code (code)}<option value={code}></option>{/each}
			</datalist>
		</label>
		{#if what === 'position'}
			<label class={label}>
				{t('typed.tabLabel')}
				<select bind:value={tab} class={field}>
					<option value="">{t('typed.tabKnown')}</option>
					<option value="Stocks">{t('asset.Stocks')}</option>
					<option value="ETFs">{t('asset.ETFs')}</option>
				</select>
			</label>
		{/if}
		<div class="flex gap-2">
			<button
				type="submit"
				disabled={busy || !figure || !currency}
				class="h-11 rounded-control bg-brand px-3 text-small font-medium text-on-brand hover:bg-brand-hover disabled:opacity-60 sm:h-8"
			>
				{busy ? t('settings.saving') : t('typed.keep')}
			</button>
			<button
				type="button"
				disabled={busy || !account || !day || (what === 'position' && !symbol)}
				onclick={() => submit(true)}
				class="h-11 rounded-control border border-line px-3 text-small text-ink-secondary hover:text-ink disabled:opacity-60 sm:h-8"
			>
				{t('typed.remove')}
			</button>
		</div>
	</form>
	{#if problem}<p class="mt-2 text-small text-loss" role="alert">{problem}</p>{/if}
	{#if done}<p class="mt-2 text-small text-gain" role="status">{done}</p>{/if}
</Panel>
