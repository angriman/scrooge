<!--
@component
The mark of a series in a legend or a list: its colour, as on the chart.
-->
<script lang="ts">
	import { cn } from './cn.js';

	let { color, class: className }: { color: string; class?: string } = $props();
</script>

<span
	class={cn('inline-block size-2.5 shrink-0 rounded-mark', className)}
	style:background-color={color}
	aria-hidden="true"
></span>
