<!--
@component
What an account held at the end of a day where no statement says it, typed by hand as
`scrooge balance` and `scrooge position` take it: its balance, or how many of one instrument
it held. Used both scoped to one account inside the side panel and unscoped in a dialog for
brand-new or unlisted accounts.
-->
<script lang="ts">
	import { errorMessage, send } from '#lib/api.js';
	import { t } from '#lib/i18n/index.svelte.js';

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
		/** Lock to one owner (hides the person selector). */
		fixedOwner?: string;
		/** Lock to one account or a broker's parts (hides the input when 1; select when > 1). */
		fixedAccounts?: string[];
		/** Whether positions may be typed (false for pension certificates). */
		allowPosition?: boolean;
		/** Currency code pre-filled before the user edits it. */
		defaultCurrency?: string;
		/** Whether to offer the Remove typed button (false when a list already has remove buttons). */
		showRemove?: boolean;
		/** Notifies parent while a request is in flight (e.g. Dialog dismissible state). */
		onbusy?: (busy: boolean) => void;
	}

	let {
		owners,
		owner,
		accounts,
		currencies,
		today,
		token,
		ondone,
		fixedOwner,
		fixedAccounts,
		allowPosition = true,
		defaultCurrency = '',
		showRemove = true,
		onbusy
	}: Props = $props();

	const uid = $props.id();

	let chosenWhat = $state<'balance' | 'position'>('balance');
	let whose = $state('');
	let typedAccount = $state('');
	let day = $state('');
	let figure = $state('');
	let typedCurrency = $state<string | null>(null);
	let symbol = $state('');
	let tab = $state('');
	let busy = $state(false);
	let problem = $state<string | null>(null);
	let done = $state<string | null>(null);

	const what = $derived(allowPosition ? chosenWhat : 'balance');
	const person = $derived(
		fixedOwner || whose || (owner && owners.includes(owner) ? owner : owners[0]) || ''
	);
	const account = $derived(
		fixedAccounts?.length
			? fixedAccounts.includes(typedAccount)
				? typedAccount
				: fixedAccounts[0]
			: typedAccount
	);
	const currency = $derived(typedCurrency ?? defaultCurrency);

	async function submit(remove: boolean) {
		busy = true;
		onbusy?.(true);
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
		onbusy?.(false);
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
		'h-8 rounded-control border border-line bg-raised px-2 text-small text-ink focus:border-brand focus:outline-none';
	const label = 'flex flex-col gap-1 text-caption text-ink-secondary';
</script>

<form onsubmit={typeIt} class="flex flex-wrap items-end gap-3">
	{#if allowPosition}
		<label class={label}>
			{t('typed.whatLabel')}
			<select bind:value={chosenWhat} class={field}>
				<option value="balance">{t('typed.balance')}</option>
				<option value="position">{t('typed.position')}</option>
			</select>
		</label>
	{/if}
	{#if !fixedOwner && owners.length > 1}
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
	{#if fixedAccounts && fixedAccounts.length > 1}
		<label class={label}>
			{t('accounts.partLabel')}
			<select
				value={account}
				onchange={(event) => (typedAccount = event.currentTarget.value)}
				class={field}
			>
				{#each fixedAccounts as name (name)}<option value={name}>{name}</option>{/each}
			</select>
		</label>
	{:else if !fixedAccounts?.length}
		<label class={label}>
			{t('investments.colAccount')}
			<input
				bind:value={typedAccount}
				required
				list="typed-accounts-{uid}"
				maxlength="80"
				class="{field} w-48"
			/>
			<datalist id="typed-accounts-{uid}">
				{#each accounts as name (name)}<option value={name}></option>{/each}
			</datalist>
		</label>
	{/if}
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
			value={currency}
			oninput={(event) => (typedCurrency = event.currentTarget.value)}
			list="typed-currencies-{uid}"
			maxlength="3"
			placeholder="CHF"
			class="{field} w-20 uppercase"
		/>
		<datalist id="typed-currencies-{uid}">
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
			class="h-8 rounded-control bg-brand px-3 text-small font-medium text-on-brand hover:bg-brand-hover disabled:opacity-60"
		>
			{busy ? t('settings.saving') : t('typed.keep')}
		</button>
		{#if showRemove}
			<button
				type="button"
				disabled={busy || !account || !day || (what === 'position' && !symbol)}
				onclick={() => submit(true)}
				class="h-8 rounded-control border border-line px-3 text-small text-ink-secondary hover:text-ink disabled:opacity-60"
			>
				{t('typed.remove')}
			</button>
		{/if}
	</div>
</form>
{#if problem}<p class="mt-2 text-small text-loss" role="alert">{problem}</p>{/if}
{#if done}<p class="mt-2 text-small text-gain" role="status">{done}</p>{/if}
