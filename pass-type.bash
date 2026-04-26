#!/usr/bin/env bash

set -uo pipefail

PASS_TYPE_MENU="bemenu -i -l 10 -p"

mapfile -t choices < <(find $PREFIX -name '*.gpg' -printf '%P\n' | sort)

gpgextre="(.+)\.gpg$"
for k in "${!choices[@]}"; do
	v="${choices[$k]}"
	if [[ $v =~ $gpgextre ]]; then
		choices[$k]=${BASH_REMATCH[1]}
	fi
done
choices=("" "${choices[@]}")

selected=$(printf "%s\n" "${choices[@]}" | $PASS_TYPE_MENU "pass")
[[ -z $selected ]] && exit 1

mapfile -t data < <(pass show "$selected")
[[ -z $data ]] && exit 1

seqs=("{Password}")
kvre="^([^:]+):\s*(.+)$"
for l in "${data[@]}"; do
	if [[ $l =~ $kvre ]]; then
		if [[ ${BASH_REMATCH[1],,} == "auto-type" ]]; then
			seqs=("${BASH_REMATCH[2]}" "${seqs[@]}")
		else
			seqs+=("{${BASH_REMATCH[1]}}")
		fi
	fi
done


seq=$(printf "%s\n" "${seqs[@]}" | $PASS_TYPE_MENU "$selected")
[[ -z $seq ]] && exit 1

printf "%s\n" "${data[@]}" | python3 pass-type.py -s "$seq"
