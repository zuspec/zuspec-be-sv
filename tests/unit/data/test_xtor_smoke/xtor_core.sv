module XtorCore(
  input logic clock,
  input logic reset,
  output logic ready,
  input logic valid,
  input logic [31:0] data_i,
  output logic [31:0] data_o
);

  always @(posedge clock or posedge reset) begin
    if (reset) begin
      data_o <= 0;
      ready <= 0;
    end else begin
      ready <= 1;
      if (valid && ready) begin
        data_o <= data_i + 1;
      end
    end
  end

endmodule
