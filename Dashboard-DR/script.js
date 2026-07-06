const cpu = document.getElementById('cpuChart');

new Chart(cpu,{

type:'line',

data:{

labels:['Mon','Tue','Wed','Thu','Fri','Sat','Sun'],

datasets:[{

label:'CPU %',

data:[34,52,41,68,44,58,36],

borderColor:'#00d084',

backgroundColor:'rgba(0,208,132,.2)',

fill:true,

tension:.4

}]

},

options:{

plugins:{

legend:{

labels:{

color:'white'

}

}

},

scales:{

x:{

ticks:{color:'white'}

},

y:{

ticks:{color:'white'}

}

}

}

});



const memory=document.getElementById('memoryChart');

new Chart(memory,{

type:'bar',

data:{

labels:['Host1','Host2','Host3','Host4'],

datasets:[{

label:'Memory %',

data:[62,74,58,81],

backgroundColor:'#00b4ff'

}]

},

options:{

plugins:{

legend:{

labels:{color:'white'}

}

},

scales:{

x:{ticks:{color:'white'}},

y:{ticks:{color:'white'}}

}

}

});


// ===================== PROFILE AVATAR =====================
// Shows initials from the logged-in user, and lets them upload a photo.
(function initAvatar() {
    const avatar = document.getElementById("avatar");
    if (!avatar) return;

    const initialsEl = document.getElementById("avatarInitials");
    const imgEl = document.getElementById("avatarImg");
    const upload = document.getElementById("avatarUpload");

    // Derive initials from the stored user (falls back to "DR Admin")
    const user = (localStorage.getItem("drUser") || "DR Admin").trim();
    const initials = user
        .split(/[\s@._-]+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((part) => part.charAt(0).toUpperCase())
        .join("");
    initialsEl.textContent = initials || "DR";

    // Restore a previously uploaded photo
    const savedPhoto = localStorage.getItem("drAvatar");
    if (savedPhoto) {
        imgEl.src = savedPhoto;
        imgEl.hidden = false;
        initialsEl.hidden = true;
    }

    // Click avatar -> open file picker
    avatar.addEventListener("click", () => upload.click());

    // Handle upload -> preview + persist
    upload.addEventListener("change", () => {
        const file = upload.files && upload.files[0];
        if (!file || !file.type.startsWith("image/")) return;

        const reader = new FileReader();
        reader.onload = (e) => {
            imgEl.src = e.target.result;
            imgEl.hidden = false;
            initialsEl.hidden = true;
            localStorage.setItem("drAvatar", e.target.result);
        };
        reader.readAsDataURL(file);
    });
})();