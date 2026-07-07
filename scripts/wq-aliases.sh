# whisperquiet shell controls (the menu bar icon hides behind the notch).
# Adopt with:  echo 'source ~/Projects/whisperquiet/scripts/wq-aliases.sh' >> ~/.zshrc
_wq() { touch "$HOME/Library/Application Support/whisperquiet/trigger-$1"; }
alias wq-camera='_wq camera'        # camera control on/off
alias wq-cursor='_wq cursor'        # head cursor on/off
alias wq-calibrate='_wq calibrate'  # redo gesture calibration
alias wq-quit='_wq quit'            # quit app (clears any stuck panel)
alias wq-settings='_wq settings'    # open Preferences
alias wq-start='open "$HOME/Applications/WhisperQuiet.app"'
alias wq-log='tail -20 "$HOME/Library/Logs/whisperquiet.log"'
alias wq-report='~/Projects/whisperquiet/.venv/bin/python ~/Projects/whisperquiet/scripts/report.py'  # dogfood scorecard
alias wq-playground='open ~/Projects/whisperquiet/playground/index.html'  # training drills
alias wq-preferences='open "http://127.0.0.1:8377/"'  # direct Preferences page
alias wq-vocab='python3 ~/Projects/whisperquiet/scripts/vocab.py'  # add words that trip dictation
