(function() {
    console.log("Vexa Speaker Event Logger: Initializing...");

    // --- Configuration ---
    const participantSelector = '.IisKdb'; // Main selector for participant container/element
    const speakingClasses = ['Oaajhc', 'HX2H7', 'wEsLMd', 'OgVli']; // Discovered speaking/animation classes
    const silenceClass = 'gjg47c';        // Class indicating the participant is silent or speech ended
    const nameSelectors = [               // Try these selectors to find the participant's name
        '.zWGUib',                        // Common name display class in Google Meet
        '.XWGOtd',                        // Another potential name class
        '[data-self-name]',               // Attribute often holding self name
        '[data-participant-id]'           // Attribute for participant ID (can be used if name not found)
    ];
    const checkInterval = 500; // ms - How often to rescan for new participants if observer misses some dynamic loads

    // --- State ---
    // Stores the logical speaking state ("speaking" or "silent") for each participant ID/element
    const speakingStates = new Map(); 

    // --- Helper Functions ---
    function getParticipantId(element) {
        let id = element.getAttribute('data-participant-id');
        if (!id) {
            const stableChild = element.querySelector('[jsinstance]');
            if (stableChild) {
                id = stableChild.getAttribute('jsinstance');
            }
        }
        if (!id) {
            if (!element.dataset.vexaGeneratedId) {
                element.dataset.vexaGeneratedId = 'vexa-id-' + Math.random().toString(36).substr(2, 9);
            }
            id = element.dataset.vexaGeneratedId;
        }
        return id;
    }

    function getParticipantName(participantElement) {
        const mainTile = participantElement.closest('[data-participant-id]');
        if (mainTile) {
            const userExampleNameElement = mainTile.querySelector('span.notranslate');
            if (userExampleNameElement && userExampleNameElement.textContent && userExampleNameElement.textContent.trim()) {
                const nameText = userExampleNameElement.textContent.trim();
                if (nameText.length > 1 && nameText.length < 50 && /^[\p{L}\s.'-]+$/u.test(nameText)) {
                    const forbiddenSubstrings = ["more_vert", "mic_off", "mic", "videocam", "videocam_off", "present_to_all"];
                    if (!forbiddenSubstrings.some(sub => nameText.toLowerCase().includes(sub.toLowerCase()))) {
                        return nameText;
                    }
                }
            }
            const googleTsNameSelectors = [
                '[data-self-name]', '.zWGUib', '.cS7aqe.N2K3jd', '.XWGOtd', '[data-tooltip*="name"]'
            ];
            for (const selector of googleTsNameSelectors) {
                const nameElement = mainTile.querySelector(selector);
                if (nameElement) {
                    let nameText = nameElement.textContent || nameElement.innerText || nameElement.getAttribute('data-self-name') || nameElement.getAttribute('data-tooltip');
                    if (nameText && nameText.trim()) {
                        if (selector.includes('data-tooltip') && nameText.includes("Tooltip for ")) {
                            nameText = nameText.replace("Tooltip for ", "").trim();
                        }
                        const forbiddenSubstrings = ["more_vert", "mic_off", "mic", "videocam", "videocam_off", "present_to_all"];
                        if (!forbiddenSubstrings.some(sub => nameText.toLowerCase().includes(sub.toLowerCase()))) {
                            return nameText.split('\n').pop()?.trim() || 'Unknown (Filtered)';
                        }
                    }
                }
            }
        }
        for (const selector of nameSelectors) {
            const nameElement = participantElement.querySelector(selector);
            if (nameElement) {
                let nameText = nameElement.textContent || nameElement.innerText || nameElement.getAttribute('data-self-name');
                if (nameText && nameText.trim()) return nameText.trim();
            }
        }
        if (participantElement.textContent && participantElement.textContent.includes("You") && participantElement.textContent.length < 20) {
            return "You";
        }
        const idToDisplay = mainTile ? getParticipantId(mainTile) : getParticipantId(participantElement);
        return `Participant (${idToDisplay})`;
    }

    function logSpeakerEvent(participantElement, mutatedClassList) {
        const participantId = getParticipantId(participantElement);
        const participantName = getParticipantName(participantElement);
        const timestamp = Date.now();
        const previousLogicalState = speakingStates.get(participantId) || "silent"; // Default to silent

        const isNowVisiblySpeaking = speakingClasses.some(cls => mutatedClassList.contains(cls));
        const isNowVisiblySilent = mutatedClassList.contains(silenceClass);

        // console.log(`%cDEBUG_EVENT for ${participantName} (ID: ${participantId}): PrevLogicState: '${previousLogicalState}', VisiblySpeaking: ${isNowVisiblySpeaking}, VisiblySilent: ${isNowVisiblySilent}, Classes: "${participantElement.className}"`, 'color: #777;');

        if (isNowVisiblySpeaking) {
            if (previousLogicalState !== "speaking") {
                console.log(`%c🎤 SPEAKER_START: ${participantName} (ID: ${participantId}) at ${new Date(timestamp).toISOString()} (Timestamp: ${timestamp})`, 'color: dodgerblue; font-weight: bold;');
                console.log(`    Classes for START: "${participantElement.className}"`);
            }
            speakingStates.set(participantId, "speaking"); // Set or maintain speaking state
        } else if (isNowVisiblySilent) {
            if (previousLogicalState === "speaking") {
                console.log(`%c🔇 SPEAKER_END: ${participantName} (ID: ${participantId}) at ${new Date(timestamp).toISOString()} (Timestamp: ${timestamp})`, 'color: orange; font-weight: bold;');
                console.log(`    Classes for END: "${participantElement.className}"`);
            }
            speakingStates.set(participantId, "silent"); // Set or maintain silent state
        } else {
            // Neither a known speaking class nor the silence class is present.
            // If previously logically "speaking", we maintain that state, assuming it's a brief flicker
            // or transition before either another speaking class or the silence class appears.
            // If it was "silent", it remains "silent" until a speaking class appears.
            // This means speakingStates.get(participantId) will retain its last logical value (speaking or silent)
            // unless explicitly changed by the conditions above.
            // console.log(`%cUNKNOWN_VISUAL_STATE for ${participantName} (ID: ${participantId}): Classes: "${participantElement.className}", PrevLogicState: '${previousLogicalState}'. Logical state unchanged.`, 'color: #aaa;');
        }
    }

    // --- Main Logic ---
    function observeParticipant(participantElement) {
        const participantId = getParticipantId(participantElement);
        
        // Determine initial logical state based on current classes
        let initialLogicalState = "silent";
        if (speakingClasses.some(cls => participantElement.classList.contains(cls))) {
            initialLogicalState = "speaking";
        } else if (participantElement.classList.contains(silenceClass)) {
            initialLogicalState = "silent";
        } // If neither, default to silent for initial state setting
        speakingStates.set(participantId, initialLogicalState);
        
        console.log(`%c👁️ Observing: ${getParticipantName(participantElement)} (ID: ${participantId}), Initial logical state: ${initialLogicalState}, Classes: "${participantElement.className}"`, 'color: teal');
        
        // If initially speaking, log a START event
        if(initialLogicalState === "speaking") {
            // Call logSpeakerEvent with the element and its current classList
            logSpeakerEvent(participantElement, participantElement.classList);
        }

        const callback = function(mutationsList, observer) {
            for (const mutation of mutationsList) {
                if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                    const targetElement = mutation.target;
                    if (targetElement.matches(participantSelector) || participantElement.contains(targetElement)) {
                        const finalTarget = targetElement.matches(participantSelector) ? targetElement : participantElement;
                        console.log(`%cCLASS_MUTATION on ${getParticipantName(finalTarget)} (ID: ${getParticipantId(finalTarget)}): New classes: "${finalTarget.className}"`, 'color: #888;');
                        // Pass the live classList object from the mutated element
                        logSpeakerEvent(finalTarget, finalTarget.classList);
                    }
                }
            }
        };

        const observer = new MutationObserver(callback);
        observer.observe(participantElement, { 
            attributes: true, 
            attributeFilter: ['class'],
            subtree: true 
        });
        
        if (!participantElement.dataset.vexaObserverAttached) {
             participantElement.dataset.vexaObserverAttached = 'true';
        }
    }

    function scanForAllParticipants() {
        const participantElements = document.querySelectorAll(participantSelector);
        participantElements.forEach(el => {
            if (!el.dataset.vexaObserverAttached) {
                 observeParticipant(el);
            } else {
                // For already observed elements, check if their current classList reflects a state
                // different from our last known logical state. This can catch missed mutations.
                const participantId = getParticipantId(el);
                const previousLogicalState = speakingStates.get(participantId);
                
                const isNowVisiblySpeaking = speakingClasses.some(cls => el.classList.contains(cls));
                const isNowVisiblySilent = el.classList.contains(silenceClass);
                let currentEffectiveState = previousLogicalState; // Assume no change unless proven

                if (isNowVisiblySpeaking) {
                    currentEffectiveState = "speaking";
                } else if (isNowVisiblySilent) {
                    currentEffectiveState = "silent";
                } // If neither, the logical state effectively hasn't changed from its last known speaking/silent state by this scan alone

                if (previousLogicalState !== currentEffectiveState) {
                    // console.log(`%cSCAN_OVERRIDE for ${getParticipantName(el)} (ID: ${participantId}) from '${previousLogicalState}' to '${currentEffectiveState}', Classes: "${el.className}"`, 'color: brown;');
                    logSpeakerEvent(el, el.classList); // Re-evaluate with current classes
                }
            }
        });

        const currentlySpeakingParticipants = [];
        speakingStates.forEach((state, id) => {
            if (state === "speaking") {
                let name = `ID ${id}`;
                try {
                    const pElement = document.querySelector(`[data-vexa-generated-id="${id}"]`) || document.querySelector(`[data-participant-id="${id}"]`);
                    if (pElement) {
                        const participantContainer = pElement.closest(participantSelector) || pElement;
                        name = getParticipantName(participantContainer);
                    }
                } catch (e) {}
                currentlySpeakingParticipants.push(name);
            }
        });
        
        if (currentlySpeakingParticipants.length > 1) {
            console.warn(`%c📊 SIMULTANEOUS SPEAKERS DETECTED (${currentlySpeakingParticipants.length}): ${currentlySpeakingParticipants.join(', ')} at ${new Date(Date.now()).toISOString()}`, 'color: purple; font-weight:bold;');
        }
    }

    // --- Initialization and Dynamic Handling ---
    scanForAllParticipants();

    const bodyObserver = new MutationObserver((mutationsList) => {
        for (const mutation of mutationsList) {
            if (mutation.type === 'childList') {
                mutation.addedNodes.forEach(node => {
                    if (node.nodeType === Node.ELEMENT_NODE) {
                        const elementNode = node;
                        if (elementNode.matches(participantSelector) && !elementNode.dataset.vexaObserverAttached) {
                            observeParticipant(elementNode);
                        }
                        elementNode.querySelectorAll(participantSelector).forEach(childEl => {
                            if (!childEl.dataset.vexaObserverAttached) {
                                observeParticipant(childEl);
                            }
                        });
                    }
                });
                mutation.removedNodes.forEach(node => {
                     if (node.nodeType === Node.ELEMENT_NODE) {
                        const elementNode = node;
                        if (elementNode.matches(participantSelector)) {
                           const participantId = getParticipantId(elementNode);
                           const participantName = getParticipantName(elementNode);
                           if(speakingStates.get(participantId) === 'speaking'){
                                // Log a synthetic SPEAKER_END if they were speaking when removed
                                console.log(`%c🔇 SPEAKER_END (Participant removed while speaking): ${participantName} (ID: ${participantId}) at ${new Date(Date.now()).toISOString()}`, 'color: darkred; font-weight: bold;');
                                console.log(`    Last known classes: "${elementNode.className}"`); // Log classes at removal
                           }
                           speakingStates.delete(participantId);
                           delete elementNode.dataset.vexaObserverAttached;
                           delete elementNode.dataset.vexaGeneratedId;
                           console.log(`%c🗑️ Removed observer for: ${participantName} (ID: ${participantId})`, 'color: red');
                        }
                        elementNode.querySelectorAll(participantSelector).forEach(childEl => {
                            const childId = getParticipantId(childEl);
                            const childName = getParticipantName(childEl);
                            if(speakingStates.get(childId) === 'speaking'){
                                console.log(`%c🔇 SPEAKER_END (Child participant removed while speaking): ${childName} (ID: ${childId}) at ${new Date(Date.now()).toISOString()}`, 'color: darkred; font-weight: bold;');
                                console.log(`    Last known classes of child: "${childEl.className}"`);
                            }
                            speakingStates.delete(childId);
                            delete childEl.dataset.vexaObserverAttached;
                            delete childEl.dataset.vexaGeneratedId;
                            console.log(`%c🗑️ Removed observer for child: ${childName} (ID: ${childId})`, 'color: red');
                        });
                    }
                });
            }
        }
    });

    const targetNode = document.body;
    if (targetNode) {
        bodyObserver.observe(targetNode, { childList: true, subtree: true });
        console.log("Vexa Speaker Event Logger: Observing document body for participant changes.");
    } else {
        console.error("Vexa Speaker Event Logger: Could not find document body to observe.");
    }

    setInterval(scanForAllParticipants, checkInterval);

    console.log("Vexa Speaker Event Logger: Running. Version 3 - Refined END logic.");
    console.log("Speaking Classes: [" + speakingClasses.join(", ") + "]");
    console.log("Silence Class: " + silenceClass);
    console.log("Look for 🎤 SPEAKER_START and 🔇 SPEAKER_END events. They should now be more accurate.");
    console.log("Look for CLASS_MUTATION logs to see all class changes on participant tiles.");
    
})(); 