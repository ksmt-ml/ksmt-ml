"""Independent sequential RTL design templates used in the BMC campaign."""

SIZES={
    "serializer": (5,8), "history-ring": (4,6), "pulse-stretcher": (4,7),
    "elastic-pipe": (4,7), "fair-arbiter": (3,5), "window-voter": (4,6),
}


def serializer(n):
    p=n.bit_length()
    return [
        f"module top(input clk, reset, inc, load, input [{n-1}:0] data);",
        f"reg [{n-1}:0] frozen=0, shifter=0;",
        f"reg [{p-1}:0] pos={n}, remaining=0;",
        "always @(posedge clk) begin",
        f"  if (reset) begin frozen<=0; shifter<=0; pos<={n}; remaining<=0; end",
        f"  else if (load) begin frozen<=data; shifter<=data; pos<=0; remaining<={n}; end",
        f"  else if (inc) begin if (pos<{n}) pos<=pos+1'b1;",
        "    if (remaining!=0) begin shifter<=shifter>>1; remaining<=remaining-1'b1; end end",
        "end",
        f"wire ref = pos<{n} ? frozen[pos] : 1'b0;",
        "wire got = remaining!=0 ? shifter[0] : 1'b0;",
    ]


def history(n):
    p=(n-1).bit_length()
    lines=[
        "module top(input clk, reset, inc, sample);",
        f"reg [{n-1}:0] stack=0, memory=0;",
        f"reg [{p-1}:0] ptr=0;",
        "always @(posedge clk) begin",
        "  if(reset) begin stack<=0; memory<=0; ptr<=0; end",
        f"  else if(inc) begin stack<={{stack[{n-2}:0], sample}}; memory[ptr]<=sample;",
        f"    ptr <= ptr=={n-1} ? 0 : ptr+1'b1; end",
        "end",
    ]
    for i in range(n):
        lines += [f"wire [{p}:0] addr{i} = ptr > {i} ? ptr - {i+1} : ptr + {n-i-1};"]
    return lines+[f"wire [{n-1}:0] ref = stack;",
                  f"wire [{n-1}:0] got = {{" + ",".join(f"memory[addr{i}]" for i in reversed(range(n))) + "};"]


def stretcher(n):
    return [
        "module top(input clk, reset, inc, pulse);",
        f"reg [{n.bit_length()-1}:0] time_left=0;",
        f"reg [{n-1}:0] history=0;",
        "always @(posedge clk) begin",
        "  if (reset) begin time_left<=0; history<=0; end",
        f"  else begin if (pulse) time_left<={n}; else if(time_left!=0) time_left<=time_left-1'b1;",
        f"    history <= {{history[{n-2}:0],pulse}}; end",
        "end",
        "wire ref = time_left != 0;",
        "wire got = |history;",
    ]


def elastic(n):
    return [
        f"module top(input clk, reset, inc, consume, input [{n-1}:0] data);",
        f"reg [{n-1}:0] q0=0, q1=0, bank0=0, bank1=0;",
        "reg [1:0] count=0;",
        "reg rd=0, wr=0, full=0;",
        "wire take = consume && count!=0;",
        "wire put = inc && (count!=2 || take);",
        "wire valid = full || rd!=wr;",
        "wire pop_now = consume && valid;",
        "wire push_now = inc && (!full || pop_now);",
        "always @(posedge clk) begin",
        "  if (reset) begin count<=0; q0<=0; q1<=0; rd<=0; wr<=0; full<=0; bank0<=0; bank1<=0; end",
        "  else begin",
        "    case ({put,take})",
        "      2'b10: begin if (count==0) q0<=data; else q1<=data; count<=count+1'b1; end",
        "      2'b01: begin q0<=q1; count<=count-1'b1; end",
        "      2'b11: begin if (count==1) q0<=data; else begin q0<=q1; q1<=data; end end",
        "      default: count<=count;",
        "    endcase",
        "    if (push_now) begin if (wr) bank1<=data; else bank0<=data; wr<=!wr; end",
        "    if (pop_now) rd<=!rd;",
        "    if (push_now && !pop_now) full<=(rd!=wr);",
        "    if (pop_now && !push_now) full<=0;",
        "  end",
        "end",
        f"wire [{n+1}:0] ref = {{count!=2, count!=0, (count!=0 ? q0 : {n}'b0)}};",
        f"wire [{n+1}:0] got = {{!full, valid, (valid ? (rd ? bank1 : bank0) : {n}'b0)}};",
    ]


def arbiter(n):
    p=(n-1).bit_length()
    lines=[
        f"module top(input clk, reset, inc, input [{n-1}:0] request);",
        f"reg [{p-1}:0] head=0;",
        f"reg [{n-1}:0] token=1;",
        f"reg [{n-1}:0] spec_grant;",
        f"reg [{p-1}:0] spec_next;",
        "integer j, k;",
        "always @* begin",
        "  spec_grant=0; spec_next=head;",
        f"  for(j={n-1};j>=0;j=j-1) begin",
        "    k={1'b0,head}+j;",
        f"    if(k>={n}) k=k-{n};",
        f"    if(request[k]) begin spec_grant={n}'d1 << k; spec_next=(k=={n-1}) ? 0 : k+1; end",
        "  end",
        "end",
    ]
    for i in range(n):
        values=[]
        for j in range(n):
            k=(i+j)%n
            guard=" && ".join([f"token[{i}]", f"request[{k}]",
                                *(f"!request[{(i+x)%n}]" for x in range(j))])
            values.append(f"(({guard}) ? {n}'d{1<<k} : {n}'d0)")
        lines.append(f"wire [{n-1}:0] g{i} = "+" | ".join(values)+";")
    lines += [f"wire [{n-1}:0] ref = spec_grant;",
              f"wire [{n-1}:0] got = "+" | ".join(f"g{i}" for i in range(n))+";",
              "always @(posedge clk) begin",
              "  if(reset) begin head<=0; token<=1; end",
              f"  else if(inc && (|request)) begin head<=spec_next; token<={{got[{n-2}:0],got[{n-1}]}}; end",
              "end"]
    return lines


def voter(n):
    p=(n+1).bit_length()
    terms=" + ".join(f"history[{i}]" for i in range(n))
    return [
        "module top(input clk, reset, inc, sample);",
        f"reg [{n-1}:0] history=0, shadow=0;",
        f"reg [{p-1}:0] ones=0;",
        "always @(posedge clk) begin",
        "  if(reset) begin history<=0; shadow<=0; ones<=0; end",
        f"  else if(inc) begin history<={{history[{n-2}:0],sample}};",
        f"    shadow<={{sample,shadow[{n-1}:1]}};",
        f"    ones<=ones+sample-shadow[0];",
        "  end",
        "end",
        f"wire [{p-1}:0] total = {terms};",
        f"wire ref = total >= {(n+1)//2};",
        f"wire got = ones >= {(n+1)//2};",
    ]


GENERATE={"serializer":serializer,"history-ring":history,"pulse-stretcher":stretcher,
          "elastic-pipe":elastic,"fair-arbiter":arbiter,"window-voter":voter}
