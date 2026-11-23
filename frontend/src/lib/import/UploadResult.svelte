<!--
@component
What became of each file just sent, and what the import found on the way: rows left as they
were, figures that differ from what is committed, a certificate's balance. An import stopped
on bills without a maturity offers to look them up and import the same files again.
-->
<script lang="ts">
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleMinus from '@lucide/svelte/icons/circle-minus';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import { day } from '#lib/format/date.js';
	import type { Upload } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';

	interface Props {
		answer: Upload;
		owner: string;
		looking: boolean;
		onlookup: (held: string) => void;
	}

	let { answer, owner, looking, onlookup }: Props = $props();

	const rows = (count: number) => (count === 1 ? '1 row is' : `${count} rows are`);
	const notes = $derived.by(() => {
		const found = answer.imported;
		if (!found) return [];
		return [
			...found.unreconciled.map(
				(account) => `${account} has rows with no balance to check them against.`
			),
			...(found.already
				? [`${rows(found.already)} on days already committed, and left as they were there.`]
				: []),
			...(found.later ? [`${rows(found.later)} booked today or later: the next import takes them.`] : [])
		];
	});
</script>

<Panel title="This import">
	{#if answer.notice}
		<p class="mb-3 text-body text-gain" role="status">{answer.notice}</p>
	{/if}
	{#each answer.errors as error (error)}
		<p class="mb-3 text-body text-loss" role="alert">{error}</p>
	{/each}
	{#if answer.undated?.held}
		<button
			type="button"
			disabled={looking}
			onclick={() => onlookup(answer.undated!.held!)}
			class="mb-3 h-9 rounded-control bg-brand px-4 text-body font-medium text-on-brand hover:bg-brand-hover disabled:opacity-60"
		>
			{looking ? 'Looking up…' : `Look up ${answer.undated.bills.join(', ')} on TreasuryDirect and import again`}
		</button>
	{:else if answer.undated}
		<p class="mb-3 text-body text-ink-secondary">
			Add when {answer.undated.bills.join(', ')} mature{answer.undated.bills.length === 1 ? 's' : ''} to
			instruments.yaml, then import the files again.
		</p>
	{/if}

	{#if answer.imported}
		<ul class="divide-y divide-line">
			{#each answer.imported.files as file, index (index)}
				<li class="flex items-start gap-3 py-2">
					{#if file.problem}
						<CircleX class="mt-0.5 size-4 shrink-0 text-loss" aria-hidden="true" />
					{:else if file.staged}
						<CircleCheck class="mt-0.5 size-4 shrink-0 text-gain" aria-hidden="true" />
					{:else}
						<CircleMinus class="mt-0.5 size-4 shrink-0 text-ink-muted" aria-hidden="true" />
					{/if}
					<span class="min-w-0 flex-1">
						<span class="block truncate text-body text-ink">{file.name}</span>
						<span class="block text-small text-ink-secondary">
							{#if file.problem}
								<span class="text-loss">Not imported: {file.problem}</span>
							{:else if !file.label || file.label === 'not a statement'}
								Not a statement, left out
							{:else if file.staged}
								{file.label}, staged
							{:else}
								{file.label}: nothing new, all of it is committed already
							{/if}
						</span>
					</span>
				</li>
			{/each}
		</ul>
		{#each answer.imported.certificates as certificate, index (index)}
			<p class="mt-3 text-body text-ink">
				{certificate.account} of {owner}:
				<Amount value={certificate.balance} currency={certificate.currency} /> on
				{day(certificate.as_of)}, read off {certificate.filename}.
				{#if !certificate.adds_up}
					<span class="text-caution">Nothing on it adds up to that figure: compare it with the paper.</span>
				{/if}
			</p>
		{/each}
		{#if notes.length || answer.imported.differences.length}
			<ul class="mt-3 space-y-1 text-small text-ink-secondary">
				{#each notes as note (note)}<li>{note}</li>{/each}
				{#each answer.imported.differences as line (line)}
					<li class="text-caution">Differs from what is committed: {line}.</li>
				{/each}
				{#if answer.imported.differences.length}
					<li>Nothing committed was changed: take the statement back to import these rows again.</li>
				{/if}
			</ul>
		{/if}
	{/if}
</Panel>
