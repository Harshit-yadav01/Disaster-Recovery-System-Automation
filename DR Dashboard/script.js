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