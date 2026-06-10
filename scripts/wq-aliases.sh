# whisperquiet shell controls (the menu bar icon hides behind the notch).
# Adopt with:  echo 'source ~/Projects/whisperquiet/scripts/wq-aliases.sh' >> ~/.zshrc
_wq() { touch "$HOME/Library/Application Support/whisperquiet/trigger-$1"; }
alias wq-camera='_wq camera'        # camera control on/off
alias wq-cursor='_wq cursor'        # head cursor on/off
alias wq-calibrate='_wq calibrate'  # redo gesture calibration
alias wq-quit='_wq quit'            # quit app (clears any stuck panel)
alias wq-start='open "$HOME/Applications/WhisperQuiet.app"'
alias wq-log='tail -20 "$HOME/Library/Logs/whisperquiet.log"'
