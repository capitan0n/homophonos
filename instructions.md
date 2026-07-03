Πρώτα ας δούμε τι ηχοσύστημα τρέχεις, γιατί η λύση διαφέρει αν είναι PipeWire ή σκέτο PulseAudio:

bashpactl info | grep "Server Name"

Αν δει PipeWire (το πιθανότερο σε σύγχρονο Manjaro KDE), προχωράμε με τον τρόπο του PipeWire.

Η ιδέα

Ένα combined sink (ή "loopback combine") είναι ένα εικονικό sink ήχου. Ό,τι στέλνεις σε αυτό, το PipeWire το αντιγράφει και το προωθεί ταυτόχρονα σε δύο (ή περισσότερα) πραγματικά sinks — δηλαδή τα δύο σου Bluetooth ηχεία. Το player (Spotify, browser κ.λπ.) βλέπει ένα μόνο "ηχείο", αλλά πίσω παίζουν και τα δύο.

Βήματα

1. Σύνδεσε και τα δύο ηχεία μέσω Bluetooth (από το KDE applet ή bluetoothctl).
2. Βρες τα ονόματα των sinks:

bashpactl list short sinks

Θα δεις κάτι σαν:

45  bluez_output.AA_BB_CC_DD_EE_FF.1   ...
46  bluez_output.11_22_33_44_55_66.1   ...

Το πρώτο πεδίο μετά το νούμερο είναι το όνομα του sink (bluez_output.XX...). Σημείωσε και τα δύο.

3. Δημιούργησε το combined sink:

bashpactl load-module module-combine-sink \
  sink_name=combined \
  slaves=bluez_output.AA_BB_CC_DD_EE_FF.1,bluez_output.11_22_33_44_55_66.1 \
  sink_properties=device.description="Dual_Speakers"
  
Ανάλυση:

module-combine-sink — το module που φτιάχνει το συνδυασμένο sink
sink_name=combined — το εσωτερικό όνομα του νέου εικονικού sink
slaves=... — τα πραγματικά sinks (τα ηχεία σου), χωρισμένα με κόμμα, χωρίς κενά
sink_properties=device.description="Dual_Speakers" — το φιλικό όνομα που θα φαίνεται στο KDE (χρησιμοποίησε _ αντί για κενά εδώ)

4. Όρισέ το ως default ώστε όλος ο ήχος να πηγαίνει εκεί:

bashpactl set-default-sink 

Ή απλά διάλεξέ το από το ηχητικό applet του KDE ("Dual Speakers").

Πιθανά προβλήματα

Latency / desync: Διαφορετικά ηχεία μπορεί να έχουν διαφορετική καθυστέρηση και να ακούγεται μικρό echo. Σε PipeWire ο συγχρονισμός είναι αρκετά καλός, αλλά αν χρειαστεί ρύθμιση γίνεται μέσω παραμέτρων latency.

Codec: Δύο A2DP streams ταυτόχρονα φορτώνουν περισσότερο τον Bluetooth controller. Αν ακούς διακοπές, ίσως βοηθήσει να ζορίσεις χαμηλότερο codec (SBC αντί AAC/aptX).

Να γίνει μόνιμο (script)
Το pactl load-module χάνεται στο reboot. Φτιάξε ένα μικρό script που να το ξανατρέχει
