#!/usr/bin/env bash
# ~/.local/bin/dual-speakers.sh

# Τα MAC των ηχείων σου με _ αντί για :
SPK1="bluez_output.AA_BB_CC_DD_EE_FF.1"
SPK2="bluez_output.11_22_33_44_55_66.1"

# Ξεφόρτωσε τυχόν παλιό combined module για να μην διπλασιαστεί
pactl list short modules | grep module-combine-sink | cut -f1 | \
  while read id; do pactl unload-module "$id"; done

# Φόρτωσε εκ νέου
pactl load-module module-combine-sink \
  sink_name=combined \
  slaves=$SPK1,$SPK2 \
  sink_properties=device.description="Dual_Speakers"

pactl set-default-sink combined


#chmod +x ~/.local/bin/dual-speakers.sh
